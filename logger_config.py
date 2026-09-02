# logger_config.py
"""Application logging — LoggingSystem 싱글턴 (DCL + Lock).

MeasurementRegistry (measurement_registry.py)와 동일한 패턴:
  - 모듈 레벨 mutable 전역변수 0건
  - RLock → Lock (재진입 불필요)
  - Double-Checked Locking

하위 호환 API (46개 소비자 무변경):
  get_logger()       → LoggingSystem.get_instance().logger
  get_logger(name)   → logging.getLogger('test_automation.<name>') — 신규
  get_log_handler()  → LoggingSystem.get_instance().log_handler
  setup_logger()     → get_logger() alias
  reset_logger()     → LoggingSystem.reset()
  LOGGER_NAME        → LoggingSystem.LOGGER_NAME
  lc._logger 등      → __getattr__ (PEP 562) 경유
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Optional

from infrastructure.logging.log_handler import InMemoryLogHandler
from infrastructure.logging.json_formatter import StructuredJsonFormatter
from infrastructure.logging.session_log_custody import (
    SESSION_DIRECTORY_TIMESTAMP_FORMAT,
    claim_session_directory,
    is_session_directory_claimed,
    reclaimable_session_directory_names,
    release_session_directory,
    session_directory_name,
)
from domain.ports.output.log_event_port import LogEventPort
from domain.ports.output.notification_event_port import NotificationEventPort
from fcc_test_contracts.common.env_loaders import read_bool
from fcc_test_contracts.common.logging_channel import LOGGER_ROOT
from fcc_test_contracts.common.logging_channel import get_logger as _channel_get_logger
from fcc_test_contracts.common.logging_channel import (
    get_server_stream_handler as _get_server_stream_handler,
    install_server_stream_handler as _install_server_stream_handler,
)


# OBS-0 (2026-05-25): environment variable SSOT for opting the structured
# JSON sink out (e.g. embedded smoke tests, low-disk constrained builds).
# Default ``True`` — production deployments are the primary consumers of
# the Loki/Datadog/ELK pipeline that this sink feeds.
ENV_STRUCTURED_JSON: str = 'FCC_LOG_STRUCTURED_JSON'

class LoggingSystem:
    """Application logging singleton — thread-safe DCL pattern.

    동일 패턴: MeasurementRegistry (measurement_registry.py)
    """

    LOGGER_NAME: str = LOGGER_ROOT    # SSOT 상수 — 이름 자체는 logging_channel 소유

    _instance: LoggingSystem | None = None
    _lock: Lock = Lock()                    # RLock 아님 — 재진입 불필요

    # ── 싱글턴 진입점 ──────────────────────────────────────────────────────

    @classmethod
    def get_instance(cls) -> LoggingSystem:
        """DCL 싱글턴. 첫 호출 시 _setup() 실행."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    inst = cls.__new__(cls)
                    inst._setup()
                    cls._instance = inst
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """테스트 격리 전용. 핸들러 정리 후 _instance = None."""
        with cls._lock:
            inst = cls._instance
            if inst is not None:
                # GUI-AR-5: dispose the log event bus before tearing the
                # logger down so any GUI/Web subscriber sees a clean shutdown.
                bus = getattr(inst, '_log_event_port', None)
                if bus is not None:
                    try:
                        bus.dispose()
                    except Exception:
                        pass
                # GUI-PG-3 (2026-05-25): same disposal contract for the
                # notification bus — its subscribers (sidebar panel + DB
                # persistence adapter) should see a clean shutdown before
                # the underlying handlers go away.
                noti_bus = getattr(inst, '_notification_event_port', None)
                if noti_bus is not None:
                    try:
                        noti_bus.dispose()
                    except Exception:
                        pass
                # 루트 로거 핸들러 정리
                for h in list(inst._logger.handlers):
                    try:
                        h.close()
                    except Exception:
                        pass
                    inst._logger.removeHandler(h)
                # OBS-0: structured handler may be attached only to the
                # root logger (already closed above). Drop the field so a
                # subsequent reset()→get_instance() cycle rebuilds it.
                inst._structured_handler = None
                # Topic 자식 로거 핸들러 정리
                for child_logger, fh in getattr(inst, '_topic_loggers', []):
                    try:
                        fh.close()
                    except Exception:
                        pass
                    child_logger.removeHandler(fh)
                # The session directory is no longer being written to, so it
                # becomes an ordinary retention candidate again. Releasing here
                # rather than leaking the handle also lets a reset()→
                # get_instance() cycle inside the same second re-claim the same
                # directory instead of reading it as somebody else's.
                release_session_directory(getattr(inst, '_session_custody', None))
                inst._session_custody = None
            cls._instance = None

    # ── 초기화 ─────────────────────────────────────────────────────────────

    def _setup(self) -> None:
        """Logger + InMemoryLogHandler + FileHandler(통합) + Topic FileHandlers 초기화."""
        self._logger = logging.getLogger(self.LOGGER_NAME)
        self._log_handler: InMemoryLogHandler | None = None
        self._log_event_port: LogEventPort | None = None
        self._notification_event_port: NotificationEventPort | None = None
        self._file_handler: Optional[logging.FileHandler] = None
        self._structured_handler: Optional[logging.FileHandler] = None
        self._topic_loggers: list[tuple] = []  # [(child_logger, fh), ...]
        # Open handle that marks this session directory as in use. ``reset()``
        # releases it; an unclean exit is released by the OS.
        self._session_custody = None

        # Own this logger even if a previous test/import left handlers on the
        # named logging.Logger before the singleton was rebuilt.
        for handler in list(self._logger.handlers):
            try:
                handler.close()
            except Exception:
                pass
            self._logger.removeHandler(handler)
        for topic in _TOPIC_LOG_TOPICS:
            child = logging.getLogger(f'{self.LOGGER_NAME}.{topic}')
            for handler in list(child.handlers):
                try:
                    handler.close()
                except Exception:
                    pass
                child.removeHandler(handler)

        formatter = _human_formatter()

        # GUI-AR-5 (2026-05-24): InMemoryLogBus 합성 + handler 주입 →
        # GuiLogAdapter/Web/CLI 가 동일 push 채널 구독. 옛 1Hz 폴링 SSOT
        # 폐기. ``in_memory_log_bus`` import 가 ``LoggingSystem._setup``
        # 중 발생하므로, 해당 모듈은 ``get_logger()`` 재진입 방지 위해
        # ``logging.getLogger`` 직접 사용 (bootstrap-safe).
        from infrastructure.adapters.driven.in_memory_log_bus import (
            InMemoryLogBus,
        )
        # GUI-PG-3 (2026-05-25): InMemoryNotificationBus 합성 — sidebar +
        # DB persistence adapter + future Web feed 가 동일 push 채널을
        # 구독. 옛 ``[ERROR]`` prefix anti-pattern 종식의 transport SSOT.
        from infrastructure.adapters.driven.in_memory_notification_bus import (
            InMemoryNotificationBus,
        )

        self._log_event_port = InMemoryLogBus()
        self._log_handler = InMemoryLogHandler(event_port=self._log_event_port)
        self._log_handler.setFormatter(formatter)
        self._notification_event_port = InMemoryNotificationBus()

        logs_dir = 'logs'
        # ``exist_ok`` rather than check-then-create: two processes that boot in
        # the same moment both saw "missing" and both created it, and the loser
        # got FileExistsError. That is not hypothetical — the per-PC mode policy
        # allows a GUI and a session node to start together, and the parallel
        # test lane reproduces it on every run.
        os.makedirs(logs_dir, exist_ok=True)

        _cleanup_old_logs(logs_dir)

        # ⚠️ **이름에 PID 가 들어간다 — 그것이 수리의 절반이다.**
        #
        # 옛 이름은 초 단위 타임스탬프뿐이었고 ``exist_ok=True`` 로 만들었다. 그 조합은
        # 경합을 **피한 것이 아니라 공유로 바꾼 것**이다: 같은 초에 시작한 두 프로세스가
        # **같은 디렉터리를 쓰고 같은 로그 파일에 교차로 기록**한다. 옛 주석은 그것을
        # 예상된 동작처럼 적었지만, 그것은 정상이 아니라 위 보존 정리 결함이 **희생자를
        # 찾는 방법**이었다 — 두 프로세스가 한 디렉터리를 공유하면 한쪽의 정리가 다른
        # 쪽의 열린 핸들러를 지운다.
        #
        # PID 는 한 호스트에서 살아 있는 프로세스를 유일하게 가른다. 재사용된 PID 는
        # 초 단위 접두가 다르므로 충돌하지 않는다. 정렬 순서는 접두가 정하므로 보존
        # 정리의 "이름 순 = 시간 순" 전제도 그대로다.
        #
        # ⚠️ 그리고 **이름을 여기서 조립하지 않는다** — 형식은
        # :mod:`infrastructure.logging.session_log_custody` 가 소유한다. 그 형식이 곧
        # 보존 정리가 *무엇을 회수 대상으로 알아보는가* 이기도 해서, 두 곳에 적히면
        # 로거가 만드는 이름과 정리가 알아보는 이름이 갈라지고 갈라진 날 회수가 조용히
        # 멈춘다.
        timestamp = datetime.now().strftime(SESSION_DIRECTORY_TIMESTAMP_FORMAT)
        session_dir = os.path.join(
            logs_dir, session_directory_name(timestamp, os.getpid()),
        )
        os.makedirs(session_dir, exist_ok=True)
        # Mark the directory as in use for this process's lifetime so retention
        # in *another* process cannot reclaim it while it is still being written
        # to. Failure is not fatal — see ``claim_session_directory``.
        self._session_custody = claim_session_directory(Path(session_dir))

        log_filename = os.path.join(session_dir, 'test_log.log')
        self._file_handler = FileHandler(log_filename, encoding='utf-8')
        self._file_handler.setLevel(logging.INFO)  # Sprint 109: 요약본 (DEBUG는 Topic 파일에)
        self._file_handler.setFormatter(formatter)

        self._logger.setLevel(logging.DEBUG)
        self._logger.addHandler(self._log_handler)
        self._logger.addHandler(self._file_handler)
        self._logger.propagate = False

        # OBS-0 (2026-05-25): structured JSON Lines sink. Same session
        # directory so log retention/rotation (``_cleanup_old_logs``)
        # applies automatically. INFO-level mirrors ``test_log.log`` —
        # backend ingesters typically don't want DEBUG firehose.
        if read_bool(os.environ, ENV_STRUCTURED_JSON, default=True):
            structured_path = os.path.join(session_dir, 'structured.jsonl')
            self._structured_handler = FileHandler(
                structured_path, encoding='utf-8',
            )
            self._structured_handler.setLevel(logging.INFO)
            self._structured_handler.setFormatter(StructuredJsonFormatter())
            self._logger.addHandler(self._structured_handler)

        # 분야별 전용 로그 파일 — AI 분석 제출용 (같은 세션 디렉터리에 저장)
        for topic in _TOPIC_LOG_TOPICS:
            topic_fh = FileHandler(
                os.path.join(session_dir, f'{topic}.log'),
                encoding='utf-8',
            )
            topic_fh.setFormatter(formatter)
            child = logging.getLogger(f'{self.LOGGER_NAME}.{topic}')
            child.addHandler(topic_fh)
            self._topic_loggers.append((child, topic_fh))

    # ── 프로퍼티 ───────────────────────────────────────────────────────────

    @property
    def logger(self) -> logging.Logger:
        return self._logger

    @property
    def log_handler(self) -> InMemoryLogHandler | None:
        return self._log_handler

    @property
    def log_event_port(self) -> LogEventPort | None:
        """``LogEventPort`` SSOT — GUI/Web/CLI 가 push 구독 진입점.

        GUI-AR-5 (2026-05-24) — composition roots resolve the bus through
        this property instead of constructing their own (otherwise the
        ``InMemoryLogHandler`` and the subscriber would publish/listen on
        different bus instances and the GUI would receive no events).
        """
        return self._log_event_port

    @property
    def notification_event_port(self) -> NotificationEventPort | None:
        """``NotificationEventPort`` SSOT — GUI/Web 가 push 구독 진입점.

        GUI-PG-3 (2026-05-25) — composition roots resolve the curated
        user-visible notification bus through this property. The producer
        (TestRunner / orchestrator) publishes a ``NotificationEntry`` once;
        all subscribers (sidebar panel, DB persistence, future Web feed)
        receive the same value object — no string prefix parsing, no
        duplicated render rules.
        """
        return self._notification_event_port

    @property
    def file_handler(self) -> Optional[logging.FileHandler]:
        return self._file_handler

    @property
    def structured_handler(self) -> Optional[logging.FileHandler]:
        """OBS-0 (2026-05-25) — structured JSON Lines sink (Loki/Datadog/ELK).

        ``None`` when ``FCC_LOG_STRUCTURED_JSON`` env var resolves to false
        or during ``reset_logger`` test windows.
        """
        return self._structured_handler


def _human_formatter() -> logging.Formatter:
    """Return the human formatter used by provider-owned file sinks."""
    return logging.Formatter(
        '%(asctime)s [%(name)s] %(levelname)-7s %(message)s'
    )


def install_server_stream_handler() -> logging.StreamHandler:
    """Compatibility wrapper for the shared server-stream capability."""
    instance = LoggingSystem.get_instance()
    return _install_server_stream_handler(instance.logger)


def get_server_stream_handler() -> Optional[logging.StreamHandler]:
    """Compatibility lookup for the shared server-stream capability."""
    instance = LoggingSystem._instance
    if instance is None:
        return None
    return _get_server_stream_handler(instance.logger)


# ── 하위 호환 모듈 레벨 API ───────────────────────────────────────────────

# Import FileHandler at module level so tests can patch it easily
FileHandler = logging.FileHandler

_LOG_FILES_TO_KEEP = 30

# AI 분석 제출용 분야별 로그 분리 — 통합 로그와 별도로 기록
# logs/{topic}/{topic}_YYYYMMDD_HHMMSS.log 형식
_TOPIC_LOG_TOPICS: tuple[str, ...] = (
    'measurement',
    'device',
    'instrument',
    'database',
    'excel',
    'reporting',
)


#: 살아 있는 프로세스가 쓰고 있다고 볼 최근 수정 유예(초).
#:
#: ⚠️ **이 상수는 편의가 아니라 결함 수리다.** 로그를 쓰는 프로세스는 자기 세션
#: 디렉터리를 **계속 건드린다**. 그러므로 "최근에 수정됐다"는 *보유자가 살아 있다* 의
#: 관측 가능한 대리이고, 보존 정리는 그런 디렉터리를 **건너뛴다**.
#:
#: 🔴 **이것이 소유권 증명이 아니라 대리라는 사실을 여기 적는다.** 유예보다 오래 침묵한
#: 살아 있는 프로세스의 디렉터리는 여전히 지워질 수 있다. 정확한 판정은 디렉터리마다
#: OS 가 반환하는 배타 락이고, 이 저장소는 그 프리미티브를
#: ``src/application/common/instrument_exclusion.py`` 에 **이미 갖고 있다**(실 Windows
#: 증거 포함). 여기서 쓰지 않은 이유는 그 모듈이 다른 축의 봉인 대상이고 그 봉인이
#: 비공개 이름과 AST 소스 검사에 닿아 있어, 이 수리의 차선에서 건드리면 그 축을 깨기
#: 때문이다. **사본을 만드는 것은 더 나쁘다** — 이 저장소가 반복해서 이름 붙인 형태다.
#: 상환 경로는 그 프리미티브를 공용 모듈로 승격하고 양쪽이 위임하는 것이며 장부가 갖는다.
#:
#: ✅ **그 상환은 2026-08-26 에 착지했다** — 프리미티브는
#: :mod:`application.common.process_file_lock` 로 승격됐고(계약 레인, 실 Windows 실측
#: 근거도 함께 이사), ``instrument_exclusion`` 과
#: :mod:`infrastructure.logging.session_log_custody` 가 **둘 다 위임**한다. 봉인이 비공개
#: 이름을 읽던 문제는 그 이름을 **파생**으로 바꿔 해소했다(어느 모듈이 분류기를 정의하든
#: 그 소스를 읽는다).
#:
#: ⚠️ **그렇다고 이 상수가 죽지 않았다.** 이제 이것은 *보관 표식이 없는* 디렉터리의 답이다
#: — 옛 형식으로 만들어진 디렉터리와, 이 수리 이전 빌드가 지금 돌고 있는 경우. 배포는 한
#: 순간에 일어나지 않으므로 이 절반을 지우면 전환 창에서 옛 빌드가 무방비다.
_LIVE_SESSION_GRACE_SECONDS = 3600.0


def _most_recent_mtime(directory: Path) -> float:
    """디렉터리와 그 안 파일들의 최신 수정 시각. 읽을 수 없으면 **지금**(= 보수적)."""
    newest = 0.0
    try:
        newest = directory.stat().st_mtime
        for child in directory.iterdir():
            try:
                newest = max(newest, child.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        # 읽을 수 없으면 **살아 있다고 가정한다** — 모르는 것을 지우는 쪽이 이 축의
        # 실패 모드였다.
        return time.time()
    return newest


def _cleanup_old_logs(
    logs_dir: str,
    keep: int = _LOG_FILES_TO_KEEP,
    *,
    grace_seconds: float = _LIVE_SESSION_GRACE_SECONDS,
    now: float | None = None,
) -> None:
    """logs_dir에서 오래된 세션 디렉터리를 삭제합니다 (최신 keep개 유지).

    타임스탬프 기반 디렉터리명(YYYYMMDD_HHMMSS_PID)을 알파벳 순으로 정렬하면
    날짜순과 일치하므로 앞쪽 항목이 오래된 것입니다.

    ⚠️ **살아 있는 보유자의 디렉터리는 지우지 않는다.** 옛 판정은 이름 순서만 보고
    ``rmtree`` 했고, 그것이 실측된 결함이었다 — 같은 코드·같은 명령이 한 실행에서
    ``35 failed``, 다른 실행에서 ``16 failed`` 를 냈고 **차이 19건이 전부 유령**이었다.
    기전은 트레이스에 있었다: ``FileHandler`` 가 사라진 디렉터리 안의 파일을 다시 열다
    ``FileNotFoundError``. 병렬 테스트 레인의 워커 여덟이 **저장소 루트의 같은 ``logs/``**
    를 공유하고, 디렉터리 수가 ``keep`` 을 넘는 순간 **새 초기화마다 남의 디렉터리를
    하나씩** 지웠으며, 가장 오래된 것은 대개 **그 실행 초반에 뜬 살아 있는 워커의 것**
    이었다. ``LoggingSystem.reset()`` 이 테스트 격리용이라 한 회귀에서 이 초기화가
    수백 번 일어난다.

    이것은 느린 테스트도 평범한 flake 도 아니라 **귀속을 양방향으로 세탁하는 채널**이다 —
    유령이 나타나면 자기 웨이브의 회귀로 읽고, 진짜 회귀가 유령 사이에 섞이면 "어차피
    유령"으로 읽힌다. 이름 단위 차분조차 양쪽 실행에 같은 유령이 뜨면 무력하다.

    ``now`` 는 **주입 가능한 관측**이다(기본값은 실제 시계) — 판정이 시계를 직접 읽으면
    유예 경계를 시험할 수 없고, 시험할 수 없는 판정은 다음 세션이 되돌린다.

    ⚠️ **보호는 두 겹이고 둘은 합성이지 중복이 아니다** (2026-08-26 승격). 위 상수 옆에
    적혀 있던 상환 경로 — *"정확한 판정은 OS 배타 락이고 상환은 그 프리미티브를 공용
    모듈로 승격하는 것"* — 이 :mod:`application.common.process_file_lock` 로 이행됐고,
    이제 이 함수는 **정확한 답을 먼저 묻는다**:

    1. **보관 락** (:func:`~infrastructure.logging.session_log_custody.is_session_directory_claimed`)
       — 살아 있는 프로세스가 그 디렉터리를 쥐고 있는가. 침묵한 지 오래인 살아 있는
       프로세스도 여기서 걸린다(대리가 못 하던 바로 그것).
    2. **최근 수정 유예** — 보관 표식이 **없는** 디렉터리의 답. 옛 형식으로 만들어진
       디렉터리와, 이 수리 이전 빌드가 지금 돌고 있는 경우가 그 대상이다. ⚠️ 배포는
       한 순간에 일어나지 않으므로 이 절반을 지우면 전환 창에서 옛 빌드가 무방비다.

    그리고 회수 후보는 **세션 이름 문법에 맞는 디렉터리만**이다 — ``logs/`` 에는 세션이
    아닌 것이 놓일 수 있고, 모르는 것을 지우는 sweep 은 데이터 손실 사고가 되는 방식이다.
    """
    reference = time.time() if now is None else now
    try:
        directories = {
            entry.name: entry
            for entry in Path(logs_dir).iterdir()
            if entry.is_dir()
        }
    except OSError:
        return
    for name in reclaimable_session_directory_names(directories, keep):
        candidate = directories[name]
        if is_session_directory_claimed(candidate):
            continue  # 살아 있는 보유자 — 정확한 답
        if reference - _most_recent_mtime(candidate) < grace_seconds:
            continue  # 보관 표식이 없다 — 대리로 보호한다
        shutil.rmtree(candidate, ignore_errors=True)


# SSOT: 로거 이름 — 이 상수를 import해서 사용하라
LOGGER_NAME: str = LoggingSystem.LOGGER_NAME


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a logger in the test_automation hierarchy.

    get_logger()           → logging.getLogger('test_automation')  (기존 동작)
    get_logger('topic')    → logging.getLogger('test_automation.topic')
    get_logger('a.b')      → logging.getLogger('test_automation.a.b')

    Child loggers inherit handlers from the root 'test_automation' logger
    via Python's logging propagation mechanism.
    """
    # Ensure root logger is initialized
    LoggingSystem.get_instance()
    if name is None:
        return LoggingSystem.get_instance().logger
    return _channel_get_logger(name)


def get_log_handler() -> InMemoryLogHandler | None:
    return LoggingSystem.get_instance().log_handler


def get_log_event_port() -> LogEventPort | None:
    """Return the singleton :class:`LogEventPort` — GUI-AR-5 push channel.

    GUI/Web/CLI composition roots subscribe via this helper. Returns ``None``
    only during ``reset_logger`` test windows; in normal operation the bus
    is always present after the first ``get_logger()`` call.
    """
    return LoggingSystem.get_instance().log_event_port


def get_notification_event_port() -> NotificationEventPort | None:
    """Return the singleton :class:`NotificationEventPort` — GUI-PG-3 push channel.

    Mirrors :func:`get_log_event_port` for the curated, severity-tagged
    user-visible feed. Composition roots subscribe their GUI/DB adapters via
    this helper so a single bus instance fans out to every consumer.
    """
    return LoggingSystem.get_instance().notification_event_port


def get_structured_handler() -> Optional[logging.FileHandler]:
    """OBS-0 — return the structured JSON Lines sink attached to the root logger.

    ``None`` when ``FCC_LOG_STRUCTURED_JSON`` env var is falsy (sink disabled).
    Exposed so tests + operations can locate the active ``structured.jsonl``
    file path via ``handler.baseFilename``.
    """
    return LoggingSystem.get_instance().structured_handler


def setup_logger() -> logging.Logger:
    """하위 호환 alias → get_logger()."""
    return get_logger()


def reset_logger() -> None:
    """C-05: 테스트 격리를 위한 logger 초기화 (테스트 코드에서만 사용)."""
    LoggingSystem.reset()


def __getattr__(name: str):
    """PEP 562: lc._logger 등 기존 코드·테스트 하위 호환.

    lc._logger      → LoggingSystem._instance._logger
    lc._log_handler → LoggingSystem._instance._log_handler
    lc._file_handler → LoggingSystem._instance._file_handler
    """
    if name in (
        '_logger', '_log_handler', '_file_handler', '_log_event_port',
        '_notification_event_port', '_structured_handler',
    ):
        inst = LoggingSystem._instance
        return getattr(inst, name, None) if inst else None
    raise AttributeError(f"module 'logger_config' has no attribute {name!r}")
