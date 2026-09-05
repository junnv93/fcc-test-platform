"""Central PostgreSQL 경계의 **공표 표면** — 설정과 연결 생성을 한 이름으로 모은다.

■ 이 모듈은 이제 파사드다 (설계서 S3, 2026-09-05)

알맹이는 둘로 갈라졌다:

  * 설정  — ``fcc_test_platform.central_db_settings`` (드라이버를 모른다)
  * 연결  — ``fcc_test_platform.infrastructure.adapters.driven.central_db_connection``

**왜 갈랐나.** 한 모듈이 둘을 함께 들고 있어서 ``application.runtime_config`` 가
설정 하나(``CentralDbConfig``)를 읽는 것만으로 ``psycopg`` 에 정적으로 닿았다.
import-linter 의 ``app-no-db`` 계약이 그 사슬을 위반 ②로 보고했다:

    application.runtime_config -> central_db_config (l.26) -> psycopg (l.135)

**왜 이 이름을 남겼나.** 이 경로는 이 상자 밖의 소비자가 있다 — 모노레포의
``src/progress_expectation_sync_composition.py`` 가
``from fcc_test_platform.central_db_config import CENTRAL_DB_ENV, CentralDbConfig,
build_central_db_connection_factory`` 로 세 이름을 함께 가져간다
(``requirements-central.txt`` 가 이 배포판을 태그로 고정한다). 분리를 이유로 공표된
이름을 없애면 그쪽이 다음 판올림에서 깨진다. 그래서 ``__all__`` 은 **분리 이전과
같은 8개**이고, 소비자는 이 분리를 보지 못한다.

⚠️ ``application/`` 아래 어떤 모듈도 이 파사드를 import 하면 안 된다. 여기는
드라이버 쪽에 닿아 있으므로 계약 위반이 되살아난다 — 설정이 필요하면
``central_db_settings`` 를 직접 import 하라. ``runtime_config`` 이 그렇게 한다.

■ frozen-exe 안전

이 모듈을 import 해도 ``psycopg`` 는 딸려 오지 않는다. 위임 대상인 driven 어댑터가
드라이버를 **함수 안에서만** 잡기 때문이다. 분리 이전과 같은 성질이다.
"""
from __future__ import annotations

from fcc_test_platform.central_db_settings import (
    CENTRAL_DB_ENV,
    CENTRAL_SYNC_DEFAULT_BATCH_LIMIT,
    DEFAULT_POLL_INTERVAL_SECONDS,
    CentralDbConfig,
    CentralHttpSyncConfig,
    ParsedCentralDsn,
    parse_central_dsn,
)
from fcc_test_platform.infrastructure.adapters.driven.central_db_connection import (
    build_central_db_connection_factory,
)


#: 분리 이전과 **같은 8개**. 이 목록이 이 파일의 존재 이유다 — 줄이면 외부 소비자가
#: 깨진다(모듈 docstring 참조).
__all__ = [
    'CENTRAL_DB_ENV',
    'CENTRAL_SYNC_DEFAULT_BATCH_LIMIT',
    'DEFAULT_POLL_INTERVAL_SECONDS',
    'CentralDbConfig',
    'CentralHttpSyncConfig',
    'build_central_db_connection_factory',
    'ParsedCentralDsn',
    'parse_central_dsn',
]
