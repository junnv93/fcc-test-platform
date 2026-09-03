"""챔버 모드 대조 — 승인은 중앙이 선언하고, 관측은 노드가 만들고, 판정은 둘을 맞춘다.

PC 단위 모드 배타 판정(운영자 2026-08-16): 챔버 PC 는 **웹 세션을 받는 PC 이거나 받지
않는 PC** 이며 한 PC 가 둘 다일 수 없다(포트 승인이 PC 마다 따로 나기 때문). 그 사실에는
축이 **둘** 있고 권위가 다르다:

===========  ==================================  ==========================
축           "이 챔버는 웹이 허용됐다"           "나는 실제로 리스너를 열었다"
권위         **중앙** (챔버 등록부 속성)         **노드** (heartbeat)
근거         회사 정책의 사실. 운영자가 관리     증거가 있는 쪽이 판정한다
===========  ==================================  ==========================

**둘의 불일치가 곧 신호다.** 한 칸으로 접으면 그 두 사실을 구분할 수 없다 — 플롯 보관
축(기대는 DB · 관측은 디스크 · 판정은 대조)과 같은 구조이고, 그 축의 판정 도메인이
파일을 열지 않는 것과 같은 이유로 **이 모듈도 DB 도 시계도 열지 않고 관측을 인자로 받는다**
(기대=중앙 등록부 / 관측=heartbeat 파생 / 판정=순수 함수 셋이 서로를 모른다).

**어휘는 provider 중립이다.** 중앙이 아는 문장은 하나뿐이다 — *이 챔버는 웹 세션을 받는가.*
받지 않는 PC 가 **무엇으로** 도는지는 provider 의 일이고 중앙은 모델링하지 않는다 —
FCC 와 KC 는 각자 자기 GUI 를 돌리고(진입점 이름까지 같다) mmWave 는 자기 프로그램을
돌리며, 그 아래 DUT 제어는 Appium · Realtek MPTool · QRCT 로 또 갈린다. 즉 *"GUI 로
돈다"* 는 **provider 를 식별하지도 못하면서** provider 어휘를 중앙에 들인다. 모델링하면 provider 가 늘 때마다 중앙
마이그레이션이 필요해진다.

**게이트가 아니다.** ``POLICY_CONFLICT`` 조차 아무것도 막지 않는다 — 시범 단계에 과하다
(운영자 판정 2026-08-16). 목적은 위반을 **보이게** 만드는 것이다.

순수 — stdlib only.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


__all__ = [
    'ChamberModeVerdict',
    'judge_chamber_mode',
]


class ChamberModeVerdict(Enum):
    """대조 결과 — 토큰이 넷인 기준은 **운영자가 할 일이 다른가** 하나다.

    (그 기준은 이 저장소가 반복해서 쓴다: 보관 판정 4-토큰 · provider 정체성 3-토큰 ·
    참조 소스 4-토큰. *"구분해야 할 것은 다른 값이 아니라 다른 조치"* 다.)

    - ``UNDECLARED`` — 승인 칸이 비었다. **운영자가 판정해야 한다.** 관측 여부와
      무관하게 이것이 먼저다: 승인이 미상이면 그 위의 어떤 것도 판정할 수 없다.
    - ``POLICY_CONFLICT`` — 승인되지 않았는데 웹 세션을 받고 있다. **정책 위반**이고
      이 축에서 **모호하지 않은 유일한 신호**다.
    - ``NOT_OBSERVED`` — 승인됐는데 노드가 관측되지 않는다. ⚠️ **"배포 미완"이 아니다** —
      heartbeat 부재는 **다섯**을 구분하지 못한다: ① 미실행 ② 네트워크 장애
      ③ 환경변수 누락 ④ 중앙 장애 ⑤ **아직 아무도 로그인해서 안 띄웠다**(출근 전·재부팅
      후의 **정상 상태**). 이름이 원인을 주장하면 운영자가 매일 아침 없는 문제를
      조사한다. 그 다섯 중 ⑤와 "로컬 프로그램으로 정상 운영 중"은 중앙이 아예 볼 수
      없다 — 로컬 프로그램에는 보고 경로가 없기 때문이고, 그것이 이 토큰이 원인을
      주장하지 않는 두 번째 이유다.
    - ``CONSISTENT`` — 승인+관측, 또는 미승인+무관측. **할 일이 없다.**
    """

    #: 승인 칸이 비어 있다 — 아무도 판정하지 않았다. **운영자가 판정해야 한다.**
    #:
    #: ⚠️ 관측 여부와 **무관하게** 이것이 먼저다: 승인이 미상이면 그 위의 어떤 것도
    #: 판정할 수 없다. 서빙 중인 미판정 챔버와 조용한 미판정 챔버가 같은 토큰인 근거는
    #: **조치가 같다**는 것이고, 관측값 자체는 봉투에 나란히 실려 잃지 않는다.
    UNDECLARED = 'UNDECLARED'

    #: 승인되지 않았는데 웹 세션을 받고 있다 — **회사 정책 위반**.
    #: 이 축에서 **모호하지 않은 유일한 신호**다.
    POLICY_CONFLICT = 'POLICY_CONFLICT'

    #: 승인됐는데 노드가 관측되지 않는다.
    #:
    #: ⚠️ **"배포 미완"이 아니다.** heartbeat 부재는 **다섯**을 구분하지 못한다 —
    #: 미실행 · 네트워크 장애 · 환경변수 누락 · 중앙 장애 · **아직 아무도 로그인해서
    #: 안 띄웠다**(출근 전·재부팅 후의 **정상 상태**). 이름이 *배포 미완*이 아니라
    #: *관측 안 됨*인 것이 그 한계를 말한다 — 이름이 원인을 주장하면 운영자가 매일 아침
    #: 없는 문제를 조사한다.
    NOT_OBSERVED = 'NOT_OBSERVED'

    #: 승인+관측, 또는 미승인+무관측. **할 일이 없다.**
    CONSISTENT = 'CONSISTENT'


def _truthy(value: object) -> bool:
    """``bool(value)`` — 다만 그것이 터지는 값에서도 답한다.

    ⚠️ **평범한 ``bool(x)`` 로는 totality 가 성립하지 않는다.** ``__bool__`` 이 예외를
    내는 객체가 오면 판정이 raise 하고, 이 함수는 운영 화면을 그리는 자리에서 챔버마다
    불리므로 그런 값 하나가 **목록 전체를 죽인다**. codex 교차 검증(2026-08-16)이
    *"어떤 입력에도 raise 하지 않는다"* 는 주장과 ``bool()`` 직접 호출의 모순을 지적했다.

    렌더할 수 없는 값은 **참으로 읽지 않는다** — 승인은 명시적 결정이고, 판독 불가를
    "승인됨"으로 접으면 그 챔버가 조용히 CONSISTENT 가 된다.
    """
    try:
        return bool(value)
    except Exception:  # noqa: BLE001 — 판정은 total 이다
        return False


def judge_chamber_mode(
    *,
    accepts_web_sessions: Optional[bool],
    observed_serving: bool,
) -> ChamberModeVerdict:
    """승인(중앙 선언) × 관측(노드 파생) → 운영자가 할 일.

    Args:
        accepts_web_sessions: 중앙 등록부의 **선언**. ``None`` 은 *"아무도 판정하지
            않았다"* 이고 ``False`` 와 **다르다** — 전자는 판정을 요구하고 후자는
            이미 내려진 결정이다. 그래서 이 인자는 bool 이 아니라 3-상태다.
        observed_serving: 그 챔버가 **지금 웹 세션을 받고 있다고 관측되는가**.
            중앙이 heartbeat 에서 파생한다(노드가 자기 리스너의 증거다).
            ⚠️ ``False`` 는 *"로컬 프로그램으로 돈다"* 를 뜻하지 **않는다** — 로컬
            프로그램에는 중앙 보고 경로가 아예 없으므로 그 사실은 이 축이 답할 수
            없다. 그것이 ``NOT_OBSERVED`` 가 원인을 주장하지 않는 이유다.

    **total** — 어떤 입력에도 raise 하지 않는다. 이 판정은 운영 화면을 그리는 자리에서
    챔버마다 불리므로, 이상한 값 하나가 목록 전체를 죽이면 안 된다.
    """
    if accepts_web_sessions is None:
        return ChamberModeVerdict.UNDECLARED
    approved = _truthy(accepts_web_sessions)
    serving = _truthy(observed_serving)
    if serving and not approved:
        return ChamberModeVerdict.POLICY_CONFLICT
    if approved and not serving:
        return ChamberModeVerdict.NOT_OBSERVED
    return ChamberModeVerdict.CONSISTENT
