"""Central PostgreSQL connection factory — the one place the driver is bound.

■ 왜 이 파일이 생겼나 (설계서 S3, 2026-09-05)

``central_db_config`` 이 «설정»과 «연결 생성»을 함께 들고 있었다. 그래서
``application.runtime_config`` 가 설정 하나(``CentralDbConfig``)를 읽으려고 그
모듈을 import 하면 **드라이버까지 정적으로 딸려 왔고**, import-linter 의
``app-no-db`` 계약이 그 사슬을 위반으로 보고했다:

    application.runtime_config -> central_db_config (l.26) -> psycopg (l.135)

grep 으로는 보이지 않는 위반이다 — 경유 모듈이 ``application`` 밖에 있다.
해소는 «분리»다: 설정은 ``central_db_settings`` 로, 연결 생성은 여기로.

■ 왜 ``infrastructure/adapters/driven/`` 인가

``.importlinter`` 의 ``broken_contract_guidance`` 가 적는 규칙 그대로다 —
«SQL 은 infrastructure 에서만 나온다». 드라이버를 잡는 코드는 driven 어댑터다.

■ frozen-exe 안전 (이 파일이 지켜야 하는 성질)

``psycopg`` 는 **함수 안에서만** import 한다. 이 모듈을 import 하는 것만으로는
PostgreSQL 드라이버가 딸려 오지 않으므로, 중앙 경로를 조립하지 않는 데스크톱
빌드(PyInstaller / Nuitka)는 드라이버 코드를 0바이트 싣는다. 이 성질은
``central_db_config`` 이 초판부터 문서로 약속한 것이고, 여기로 옮겨서도 유지된다.

■ 이 파일이 «유일한» 구현이다

2026-09-05 이전에는 같은 lazy-connect 로직이 **두 벌** 있었다
(``central_db_config.build_central_db_connection_factory`` 와
``api_composition._build_central_connection_factory``). 둘 다 여기로 위임한다 —
공표된 이름 셋은 그대로 두고 구현만 하나로 모았다.
"""
from __future__ import annotations

from typing import Callable

from fcc_test_kernel.domain.ports.output.platform_database_port import DbConnection


__all__ = ['build_central_db_connection_factory']


def build_central_db_connection_factory(database_url: str) -> Callable[[], DbConnection]:
    """중앙 DSN 하나를 ``() -> DbConnection`` 팩토리로 바꾼다.

    호출마다 새 연결을 연다. 읽기 어댑터가 SELECT 하나를 끝내고 닫으므로 연결이
    읽기 사이에 공유되지 않는다.

    Raises:
        ValueError: ``database_url`` 이 비었을 때. 조립 시점에 크게 실패시켜
            「모든 요청에서 죽는 런타임」이 만들어지지 않게 한다.
    """
    if not database_url:
        raise ValueError('database_url is required to build a connection factory')
    import psycopg  # lazy — keeps desktop frozen-exe free of the PostgreSQL driver

    def _connect() -> DbConnection:
        return psycopg.connect(database_url)

    return _connect
