"""Live-PostgreSQL proof for provider-identity-coherence (2026-08-25).

The SQLite shim cannot answer this: the defect is that a JOIN filter and a
missing provider produce the same empty result, and any store reproduces that.
What the live run adds is the counterfactual measured on the SAME database the
screen talks to — the old read path answering 0 rows where the new one names the
reason.

⚠️ 이것은 `scripts/platform_provider_identity_live_proof.py` 의 **알맹이**다. `scripts/` 는 패키지가 아니라
**휠이 나르지 못한다** — 이 레인을 핀으로 받는 소비자에게 그 파일은 오지 않는다.
그래서 로직은 여기 살고 `scripts/` 에는 진입점만 남는다. 껍데기는 양쪽 레포에
두되, 담긴 것이 그뿐이라 **갈라질 것이 없다.**
"""
import json, sys
import psycopg
from fcc_test_platform.application.central_reference_read_adapter import PostgresCentralReferenceReadAdapter
from fcc_test_platform.application.central_reference_write_adapter import PostgresCentralReferenceWriteAdapter
from fcc_test_platform.application.central_reference_service import CentralReferenceService
from fcc_test_platform.domain.ports.output.central_reference_port import (
    CentralReferenceError, ReferenceProviderNotFoundError,
    ReferenceProviderNotRegisteredError,
)

def offered_provider_ids():
    """What this deployment's picker offers, asked of the composed registry.

    Through the composition root rather than by importing the provider builder:
    the root is the single place allowed to know that builder, and this script
    belongs to the platform lane. Going through it also makes the proof use the
    very registry the running API uses, which is the fact under test.
    """
    from fcc_test_contracts.common.auth_config import HttpAuthConfig
    from fcc_test_platform.central_db_config import CentralDbConfig
    from fcc_test_platform.application.runtime_config import PlatformApiConfig
    from fcc_test_platform.api_composition import create_platform_runtime

    def _unused():  # pragma: no cover — the registry needs no database
        raise AssertionError('the registry must not need a database')

    runtime = create_platform_runtime(
        PlatformApiConfig(
            central=CentralDbConfig(
                database_url='postgresql://unused/registry-only',
                provider_id='unused-for-this-probe',
            ),
            auth=HttpAuthConfig(),
            allow_insecure=True,
        ),
        connection_factory=_unused,
    )
    return runtime.api_adapter._provider_ui_descriptor_registry.provider_ids()  # noqa: SLF001


_USAGE = (
    'usage: platform_provider_identity_live_proof.py <POSTGRES_DSN>\n'
    '  라이브 PostgreSQL 한 대가 필요하다 — SQLite shim 은 이 축을 답하지 못한다:\n'
    '  결함이 「JOIN 필터」와 「없는 provider」를 같은 빈 결과로 만드는 것이고,\n'
    '  어떤 저장소든 그 빈 결과는 재현하기 때문이다.'
)


def main(argv: list[str] | None = None) -> int:
    """라이브 PostgreSQL 증명을 한 번 돌리고 JSON 을 낸다.

    ⚠️ **이 본문은 예전에 «모듈 최상위»에 있었다** (2026-09-05 이관 시 감쌌다).
    `scripts/` 에 있을 때는 아무도 import 하지 않아 드러나지 않았지만, 휠이 이
    모듈을 나르기 시작하면 **import 하는 것만으로 증명이 돌아간다** — 라이브 DB 에
    붙고, 없으면 그 자리에서 죽는다. 그래서 감쌌다.

    ⚠️ 종료 코드는 **0 그대로**다. 판정은 JSON 의 `ok` 필드가 나른다 — 그것을
    종료 코드로 옮기는 것은 이관이 아니라 계약 변경이고, 이 웨이브의 일이 아니다.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    # ⚠️ `--help` 를 DSN 으로 읽으면 안 된다. 이 도구는 인자 파서가 없어(위치 인자
    # 하나뿐) 도움말이 «인자처럼» 들어온다 — 그러면 도움말을 물었는데 라이브
    # 연결을 시도하고 엉뚱한 곳에서 죽는다(2026-09-05 실측).
    if any(a in ('-h', '--help') for a in args):
        print(_USAGE)
        return 0
    if len(args) != 1:
        print(_USAGE, file=sys.stderr)
        return 2
    DSN = args[0]

    UNLICENSED_PROVIDER_ID = offered_provider_ids()[0]
    factory = lambda: psycopg.connect(DSN)
    read = PostgresCentralReferenceReadAdapter(factory)
    OFFERED = (UNLICENSED_PROVIDER_ID, 'fcc-mmwave-conducted')
    svc = CentralReferenceService(
        read, PostgresCentralReferenceWriteAdapter(factory),
        bundle_provider_id=UNLICENSED_PROVIDER_ID,
        offered_provider_ids=lambda: OFFERED,
    )
    out = {}
    with psycopg.connect(DSN) as c:
        out['server_version'] = c.execute('SHOW server_version').fetchone()[0]
        out['registered_providers'] = [r[0] for r in c.execute(
            'SELECT provider_id FROM providers').fetchall()]
    out['offered_by_picker'] = list(OFFERED)
    rows, _ = svc.list_revisions(UNLICENSED_PROVIDER_ID)
    out['coherent_listing_rows'] = len(rows)
    out['coherent_families'] = len(svc.list_reference_families(UNLICENSED_PROVIDER_ID))

    def probe(label, fn):
        try:
            fn(); out[label] = 'NOT REFUSED (defect)'
        except ReferenceProviderNotRegisteredError:
            out[label] = 'ReferenceProviderNotRegisteredError -> 404 REFERENCE_PROVIDER_NOT_REGISTERED'
        except ReferenceProviderNotFoundError:
            out[label] = 'ReferenceProviderNotFoundError -> 404 NOT_FOUND'

    probe('offered_but_unregistered__list', lambda: svc.list_revisions('fcc-mmwave-conducted'))
    probe('offered_but_unregistered__families', lambda: svc.list_reference_families('fcc-mmwave-conducted'))
    probe('offered_but_unregistered__bundle', lambda: svc.build_bundle('fcc-mmwave-conducted', 'chamber-1'))
    probe('neither_side_knows_it', lambda: svc.list_revisions('nobody-knows-this'))
    out['counterfactual_old_read_path'] = (
        f'{len(read.list_revisions("fcc-mmwave-conducted"))} rows == HTTP 200 [] '
        '(indistinguishable from "registered, nothing published")')
    def boom(): raise OSError('connection refused')
    try:
        PostgresCentralReferenceReadAdapter(boom).provider_exists('x')
        out['outage_is_not_absence'] = 'answered False (defect)'
    except CentralReferenceError:
        out['outage_is_not_absence'] = 'CentralReferenceError (loud, not False)'
    out['ok'] = (
        out['coherent_listing_rows'] > 0
        and out['offered_but_unregistered__list'].startswith('ReferenceProviderNotRegistered')
        and out['neither_side_knows_it'].startswith('ReferenceProviderNotFound')
        and out['counterfactual_old_read_path'].startswith('0 rows')
    )
    print(json.dumps(out, indent=2))
    return 0


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
