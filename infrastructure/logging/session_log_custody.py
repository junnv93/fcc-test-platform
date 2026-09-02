"""로그 보존은 *끝난* 세션의 정책이다 — 살아 있는 세션은 회수 대상이 아니다.

**결함 (2026-08-26 실측).** :func:`logger_config._cleanup_old_logs` 는 세션 디렉터리를
이름순으로 정렬해 최신 30개만 남기고 나머지를 ``shutil.rmtree`` 했다. ``pytest -n 8`` 에서
워커 여덟이 **저장소 루트의 같은 ``logs/``** 를 공유하므로, 디렉터리 수가 30 을 넘는 순간
새로 뜨는 모든 초기화가 남의 디렉터리를 하나씩 지운다 — 그리고 가장 오래된 것은 대개
**그 실행 초반에 뜬, 아직 살아 있는 워커의 것**이다. 희생자의 다음 ``logger.info(...)`` 가
파일을 다시 열다 ``FileNotFoundError`` 로 죽고, 그 로거를 지나는 테스트가 전부 실패한다.
같은 코드·같은 명령이 한 실행에서 ``35 failed``, 다른 실행에서 ``16 failed`` 였고 차이
19건이 전부 유령이었다.

⚠️ **테스트만의 문제가 아니다.** PC 단위 모드 배타 정책(CLAUDE.md §Deployment Policy 3)이
**명시적으로 허용**하는 형상 — 한 챔버 PC 에서 GUI 와 세션 노드가 함께 뜨는 순간 — 에서
같은 결함이 그대로 성립한다. 그래서 상환 방향 (c)(테스트 레인에서 ``logs/`` 를 워커별 tmp
로 리디렉트)는 **편의이지 수리가 아니다**.

⚠️ **같은 축을 두 번 고치면서 한쪽만 고친 형태였다.** ``_cleanup_old_logs`` 바로 위 주석이
**생성 경합**을 이미 이름으로 알고 있고 *"the parallel test lane reproduces it on every
run"* 이라고 적으며 ``exist_ok=True`` 로 고쳤다. **삭제 경합은 손대지 않았다** — 그리고
삭제 쪽이 더 나쁘다(생성 실패는 시끄럽게 죽고, 삭제는 남을 조용히 죽인다).

**정공.** 회수 후보에서 *살아 있는 소유자가 있는 디렉터리*를 뺀다. 살아 있음의 관측은
디렉터리 안의 :data:`SESSION_LOCK_BASENAME` 에 걸린 **OS 파일 락**이고, 원시연산은
:mod:`application.common.process_file_lock` 하나가 소유한다(계측기 배타와 같은 자리).

⚠️ **PID 생존 조회는 기각했다.** Windows 에서 ``os.kill(pid, 0)`` 은 시그널을 보내지 않고
``TerminateProcess`` 를 부르므로 *살아 있는지 물어보는 코드가 그 프로세스를 죽인다* —
그리고 이 앱의 배포 대상이 Windows 다. PID 는 재사용되기도 하므로 "죽은 소유자의 번호를
물려받은 무관한 프로세스"를 구분할 수도 없다. OS 파일 락은 두 문제를 동시에 없앤다:
비정상 종료 시 커널이 자동으로 푼다.

⚠️ **PID 는 그래도 이름에 들어간다 — 진단이 아니라 기전이다.** 세션 이름은 초 단위
타임스탬프라 같은 초에 뜬 두 프로세스가 **같은 이름**을 요구하고, 그러면 둘이 한 디렉터리를
공유해 락이 성립하지 않는다(뒤에 온 쪽이 자기 디렉터리를 "남이 쥐고 있다"로 읽는다).

**모든 미상은 보존으로 답한다.** 방향이 비대칭이기 때문이다 — 과보호는 다음 sweep 이 다시
보지만, 저보호는 **살아 있는 세션의 로그를 지운다**. 그래서 관측 자체가 실패한 디렉터리도,
세션 이름 문법 밖의 디렉터리도 회수하지 않는다(업로드 GC 정책의 ``KEEP_UNRECOGNISED`` 와
같은 근거: 모르는 것을 지우는 sweep 은 데이터 손실 사고가 되는 방식이다).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import IO, Iterable, Optional

from fcc_test_contracts.common.process_file_lock import (
    lock_handle_exclusive_nonblocking,
    unlock_handle,
)


__all__ = [
    'SESSION_DIRECTORY_NAME_PATTERN',
    'SESSION_DIRECTORY_TIMESTAMP_FORMAT',
    'SESSION_LOCK_BASENAME',
    'SESSION_OWNER_PID_PREFIX',
    'claim_session_directory',
    'is_session_directory_claimed',
    'is_session_directory_name',
    'reclaimable_session_directory_names',
    'release_session_directory',
    'session_directory_name',
]


#: 세션 디렉터리 이름의 시각 부분. **이 리터럴은 여기 한 번만 적힌다** — 소비자가 다시
#: 적으면 이름을 만드는 쪽과 알아보는 쪽이 갈라지고, 갈라진 날 회수가 조용히 멈춘다.
SESSION_DIRECTORY_TIMESTAMP_FORMAT = '%Y%m%d_%H%M%S'

#: 소유 프로세스 표시 접두. 위 §PID 문단이 사유를 갖는다.
#:
#: ⚠️ ``'_p'`` 가 아니라 ``'_'`` 인 것은 취향이 아니다 — 이 형식은 2026-08-26 에
#: ``logger_config`` 에 **먼저 착지**했고(다른 세션의 대리 수리), 이 모듈은 그 형식을
#: 소유권만 가져왔다. 착지한 형식을 이유 없이 바꾸는 것은 순수한 churn 이다.
SESSION_OWNER_PID_PREFIX = '_'

#: 소유자가 프로세스 수명 동안 잡는 표식. 디렉터리 **안**에 있는 것이 요점이다 —
#: 회수 판정이 그 디렉터리 자신에게 물을 수 있어야 한다.
SESSION_LOCK_BASENAME = 'session.lock'

#: 회수 대상 문법. 옛 이름(``YYYYMMDD_HHMMSS``)도 대상이며, 그것이 무손실 이전의 조건이다.
SESSION_DIRECTORY_NAME_PATTERN = re.compile(
    r'^\d{8}_\d{6}(?:' + re.escape(SESSION_OWNER_PID_PREFIX) + r'\d+)?$'
)


def session_directory_name(timestamp: str, pid: int) -> str:
    """``YYYYMMDD_HHMMSS_{pid}``.

    시각이 앞에 오므로 이름의 사전식 정렬이 시각순 정렬과 일치한다 — 회수 순서가 그
    사실에 의존한다.
    """
    return f'{timestamp}{SESSION_OWNER_PID_PREFIX}{pid}'


def is_session_directory_name(name: str) -> bool:
    """이 이름이 *우리가 만든 세션 디렉터리*인가."""
    return bool(SESSION_DIRECTORY_NAME_PATTERN.match(name))


def reclaimable_session_directory_names(
    names: Iterable[str], keep: int,
) -> tuple[str, ...]:
    """최신 ``keep`` 개를 제외한 **세션** 디렉터리 이름 — 오래된 것부터.

    ⚠️ 세션 문법 밖의 이름은 후보에 **들어가지도 않고 ``keep`` 을 소모하지도 않는다**.
    소모하게 만들면 ``logs/`` 에 놓인 무관한 디렉터리 하나가 우리 세션 하나를 밀어낸다.

    ⚠️ 갓 만든 디렉터리가 자기 sweep 에 걸리는 일은 **구조적으로 없다** — 그 이름은
    시각순 정렬에서 언제나 최댓값이므로 ``keep >= 1`` 인 한 이 슬라이스에 들어갈 수 없다.
    """
    if keep <= 0:
        return tuple(sorted(name for name in names if is_session_directory_name(name)))
    sessions = sorted(name for name in names if is_session_directory_name(name))
    return tuple(sessions[:-keep])


def claim_session_directory(session_dir: Path) -> Optional[IO]:
    """이 디렉터리를 이 프로세스가 쓰는 중이라고 표시한다. 실패하면 ``None``.

    ⚠️ **실패는 로깅 부팅을 실패시키지 않는다.** 락 파일을 못 세웠다는 것은 *내 로그가
    남에게 회수될 수 있다*는 뜻이지 측정이 틀린다는 뜻이 아니고, 로깅이 못 뜨면 아무것도
    못 한다. 이것은 계측기 배타가 fail-closed 인 것과 **반대 방향이며 사유가 다르다** —
    거기서는 통과가 조용히 틀린 측정을 낳고, 여기서는 거부가 아무 이득 없이 앱을 죽인다.

    반환된 핸들은 **열린 채로 유지해야 한다** — 잠금은 핸들이 살아 있는 동안만 유효하다.
    """
    lock_path = session_dir / SESSION_LOCK_BASENAME
    try:
        handle = lock_path.open('a+')
    except OSError:
        return None
    try:
        lock_handle_exclusive_nonblocking(handle)
    except OSError:
        handle.close()
        return None
    return handle


def release_session_directory(handle: Optional[IO]) -> None:
    """보관을 놓는다 (안 가졌으면 아무것도 하지 않는다).

    ⚠️ 락 파일은 지우지 않는다 — 사유는 :mod:`application.common.process_file_lock`.
    """
    if handle is None:
        return
    try:
        unlock_handle(handle)
    except OSError:
        pass
    finally:
        try:
            handle.close()
        except OSError:
            pass


def is_session_directory_claimed(session_dir: Path) -> bool:
    """살아 있는 프로세스가 이 디렉터리를 쓰는 중인가.

    네 갈래이고 **셋이 보존 쪽**이다:

    * 락 파일이 없다 → ``False``. 옛 이름 디렉터리이거나 우리가 만들지 않은 것이고,
      회수 가능해야 무손실 이전이 성립한다.
    * 잠글 수 있다 → ``False``. 즉시 풀고 닫는다 — 이 함수는 **관측**이지 획득이 아니다.
    * 잠글 수 없다 → ``True``. 충돌이든 환경 문제든 **여기서는 같은 답**이다.

    ⚠️ 마지막 갈래에서 :func:`application.common.process_file_lock.is_conflict` 를 부르지
    **않는 것이 의도**다. 계측기 배타는 그 구분이 load-bearing 이다 — 운영자가 할 일이
    다르다(다른 프로세스를 닫는다 vs 환경을 고친다). 이 축에서 할 일은 **양쪽 다 같다**:
    지우지 않는다. 답을 바꿀 수 없는 분기를 적으면 이 저장소가 이미 이름 붙인
    *안전망처럼 읽히는 죽은 코드*가 된다.
    """
    lock_path = session_dir / SESSION_LOCK_BASENAME
    try:
        handle = lock_path.open('r+')
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        try:
            lock_handle_exclusive_nonblocking(handle)
        except OSError:
            return True
        unlock_handle(handle)
        return False
    finally:
        try:
            handle.close()
        except OSError:
            pass
