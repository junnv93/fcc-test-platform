"""FE-P8 invariants — Platform RBAC membership + operator provenance + audit log.

Three interlocking invariants:

1. **Membership-based authorization** — PlatformApiAdapter.authorize unions
   token-borne permissions with permissions granted by central project
   membership (read through ``project_member_permissions`` view).
   Backward-compatible: when membership is empty / unwired, the token path
   alone decides (FE-P0d behaviour).

2. **Operator provenance** — claim acquire / release lock-holder operator
   derives from the authenticated principal subject, NEVER the request body
   (a client cannot spoof an operator they are not).

3. **Audit log atomicity** — every platform write (claim acquire / release +
   membership assign / revoke) appends an ``audit_events`` row in the SAME
   DB transaction as the primary mutation. A failed audit INSERT rolls the
   primary write back; a no-op revoke does NOT emit audit.

The tests use a SQLite in-memory fixture that mirrors the central schema (the
schema contract test already proves the schema↔view↔seed are coherent;
this file proves the application code wires them correctly).
"""
from __future__ import annotations

import ast
import json
import sys
import unittest
from tests._moved_module_source import moved_module_source
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / 'src'))

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402
from fcc_test_contracts.common.sqlite_connection_factory import SqliteConnectionContext  # noqa: E402


def _platform_application_roots() -> tuple[Path, ...]:
    """이 파일의 세 AST 봉인이 훑는 자리 — **두 디렉터리**다.

    ⚠️ 오랫동안 `src/application/platform` **하나**였다. S3(2026-09-05, PR #78)가
    DB 어댑터를 `infrastructure/adapters/driven/` 으로 옮기면서 그 접두사 «밖»에
    application 관심사가 살 수 있는 길이 열렸고, `.importlinter` 계약 3 주석이
    그 위험을 적어 두었다: 감사 이벤트나 role 리터럴을 쓰는 어댑터를 그리로 옮기면
    **조용히 스캔 밖으로 나간다.**

    ⚠️ **세 봉인이 균질하지 않다** — 실측으로 갈랐다(어댑터 하나를 driven 으로
    옮겼다고 가정하고 세 검사를 돌렸다):

        검사                        형태                    이동했을 때
        role 리터럴 부재            offenders == []         **조용히 초록**
        audit_events 금지 동사      offenders == []         **조용히 초록**
        event_type 등호             emitted == allowed      빨강(누락 토큰을 댄다)

    공집합형 둘은 파일이 빠지면 위반도 함께 빠져 초록이 유지된다. 등호형 하나만
    스스로 시끄럽다. 그러므로 **범위를 넓히는 일은 앞의 둘을 위해 필요하다.**

    ⚠️ **모노레포 경로로 순진하게 넓히면 넓혀지지 않는다.** 이 레인에는 driven 에
    대응하는 장부 항목이 없어 `resolve_repo_artifact` 가 «실재하지 않는» 경로를
    문자열 그대로 돌려주고, `Path.rglob()` 은 없는 디렉터리에 **예외 없이 빈 목록**을
    준다 — 안 넓히면서 넓힌 것처럼 보인다. 그래서 후보를 **실재로 거른다**.
    (장부 자체는 고치지 않는다: 한 디렉터리에서 파일 하나만 옮기면 해소기가
    `RelocationAmbiguity` 로 거부한다 — `.importlinter` 계약 3 주석 참조.)

    실측 2026-09-05 — 넓혀도 방출 집합이 안 변한다(등호 유지):
        레인    : application 73파일 + driven 5파일  → role 0 · 동사 0 · 토큰 7/7
        모노레포: application  0파일 + driven 42파일 → role 0 · 동사 0
    """
    candidates = (
        resolve_repo_artifact(__file__, 'src/application/platform'),
        # 모노레포 배치. 이 레인에서는 장부에 항목이 없어 그대로 돌아오고 실재하지 않는다.
        resolve_repo_artifact(__file__, 'src/infrastructure/adapters/driven'),
        # 레인 배치. S3 가 어댑터를 옮긴 실제 자리.
        project_root / 'fcc_test_platform' / 'infrastructure' / 'adapters' / 'driven',
    )
    return tuple(c for c in candidates if c.is_dir())


_FORBIDDEN_AUDIT_VERBS = (
    'UPDATE "AUDIT_EVENTS"',
    'DELETE FROM "AUDIT_EVENTS"',
    'MERGE INTO "AUDIT_EVENTS"',
    'TRUNCATE "AUDIT_EVENTS"',
)


def _forbidden_audit_verbs(source: str) -> list[str]:
    """`audit_events` 를 append-only 로 깨뜨리는 SQL 동사를 찾는다.

    ⚠️ **이 매처는 2026-09-05 까지 «죽어 있었다».** 금지 동사를 소문자
    (`'DELETE FROM "audit_events"'`)로 적어 놓고 말뭉치는 `source.upper()` 로
    대문자화해 비교했다 — 대문자 건초더미에서 소문자 바늘을 찾으니 **어떤 입력에도
    매치되지 않는다.** 그래서 그 봉인은 「위반 0건」을 «영원히» 보고했고, 그것은
    「아무것도 검사하지 않았다」와 같은 값이었다(설계서 §7 교훈 ③).

    적대적 실측: driven 어댑터에 `DELETE FROM "audit_events"` 를 주입했는데 봉인이
    **초록이었다**. 같은 주입으로 role 리터럴 검사는 빨개졌다 — 그 대조가 이 결함을
    드러냈다.

    그래서 매칭을 순수 함수로 빼냈다. 아래 `TestTheAuditVerbMatcherHasTeeth` 의
    양팔이 이 함수를 «직접» 부른다: 하나는 알려진 위반에서 «듣는가», 다른 하나는
    정당한 append-only 에서 «안 듣는가». 매처가 다시 공허해지면 첫째 팔이 빨개진다.
    """
    if 'audit_events' not in source.lower():
        return []
    haystack = source.upper().replace("'", '"')
    return [verb for verb in _FORBIDDEN_AUDIT_VERBS if verb in haystack]


def _iter_scanned_sources():
    """`_platform_application_roots()` 아래 모든 `*.py` — 세 봉인의 공통 말뭉치."""
    for root in _platform_application_roots():
        yield from root.rglob('*.py')

from fcc_test_contracts.common.access_policy import ApiAccessPolicy, ApiPrincipal  # noqa: E402
from fcc_test_kernel.application.central_contract.api_contracts import (  # noqa: E402
    PLATFORM_API_OPERATIONS,
)
from fcc_test_platform.application.central_audit_write_adapter import (  # noqa: E402
    AUDIT_EVENT_COLUMNS,
    PostgresCentralAuditWriteAdapter,
)
from fcc_test_platform.application.central_claim_write_adapter import (  # noqa: E402
    PostgresCentralClaimWriteAdapter,
)
from fcc_test_platform.application.central_claim_write_service import (  # noqa: E402
    ClaimWriteService,
)
from fcc_test_platform.application.central_membership_write_adapter import (  # noqa: E402
    PostgresCentralMembershipWriteAdapter,
)
from fcc_test_platform.application.central_membership_write_service import (  # noqa: E402
    MembershipNotFoundError,
    MembershipRoleUnknownError,
    MembershipUserUnknownError,
    MembershipWriteService,
)
from fcc_test_platform.application.central_rbac_read_adapter import (  # noqa: E402
    PostgresCentralRbacReadAdapter,
)
from fcc_test_platform.application.central_rbac_read_service import (  # noqa: E402
    CentralRbacReadService,
)
from fcc_test_platform.application.central_read_adapter import (  # noqa: E402
    PostgresCentralReadAdapter,
)
from fcc_test_platform.application.central_read_service import CentralReadService  # noqa: E402
from fcc_test_platform.application.rbac_role_catalog import (  # noqa: E402
    PROJECT_ROLE_KEYS,
    is_known_role,
    permissions_for,
)
from fcc_test_platform.api.platform_routes import (  # noqa: E402
    PlatformApiAdapter,
    PlatformAuthorizationError,
    api_error_status,
)


# ── helpers ────────────────────────────────────────────────────────────────


_PROJECT_ID = '11111111-1111-1111-1111-111111111111'
_OTHER_PROJECT_ID = '22222222-2222-2222-2222-222222222222'


from support.central_pg_sqlite_shim import QmarkConnection  # noqa: E402


def _build_central_fixture(db_path: str) -> None:
    """Minimal central-schema mirror for FE-P8 tests.

    Creates the tables / views the adapters touch. Seeds the role catalog
    from the rbac_role_grants SSOT (loaded via permissions_for) so the
    project_member_permissions view returns correct grants.
    """
    with SqliteConnectionContext(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                issuer TEXT NOT NULL DEFAULT 'urn:fcc:identity:legacy',
                subject TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                UNIQUE (issuer, subject)
            );
            CREATE TABLE roles (
                id TEXT PRIMARY KEY,
                role_key TEXT UNIQUE NOT NULL
            );
            CREATE TABLE permissions (
                id TEXT PRIMARY KEY,
                permission_key TEXT UNIQUE NOT NULL
            );
            CREATE TABLE role_permissions (
                role_id TEXT NOT NULL,
                permission_id TEXT NOT NULL
            );
            CREATE TABLE project_membership (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                role_key TEXT NOT NULL,
                team TEXT,
                assigned_at TEXT NOT NULL,
                expires_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE (project_id, user_id, role_key)
            );
            CREATE TABLE claim_events (
                id TEXT PRIMARY KEY, claim_id TEXT, project_id TEXT,
                technology TEXT, condition_hash TEXT, operator TEXT,
                action TEXT, reason TEXT, occurred_at TEXT, expires_at TEXT,
                session_id TEXT, created_at TEXT
            );
            CREATE TABLE audit_events (
                id TEXT PRIMARY KEY, event_type TEXT NOT NULL, project_id TEXT,
                actor_subject TEXT NOT NULL, target_user_subject TEXT,
                target_claim_id TEXT, role_key TEXT, detail_json TEXT,
                occurred_at TEXT NOT NULL, created_at TEXT NOT NULL
            );
            """
        )
        # active_claims view (from schema SSOT — same as test_platform_claim_write_fe_p3
        # uses; abbreviated here to avoid pulling in the full _pg_view_to_sqlite).
        conn.execute("""
            CREATE VIEW active_claims AS
            SELECT ranked.project_id, ranked.claim_id, ranked.technology,
                   ranked.condition_hash, ranked.operator, ranked.occurred_at,
                   ranked.expires_at, ranked.session_id
            FROM (SELECT acquired.project_id, acquired.claim_id, acquired.technology,
                         acquired.condition_hash, acquired.operator, acquired.occurred_at,
                         acquired.expires_at, acquired.session_id,
                         ROW_NUMBER() OVER (PARTITION BY acquired.project_id, acquired.condition_hash
                                            ORDER BY acquired.occurred_at DESC) AS rn
                  FROM claim_events acquired
                  WHERE acquired.action = 'acquired'
                  AND NOT EXISTS (SELECT 1 FROM claim_events later
                                  WHERE later.claim_id = acquired.claim_id
                                  AND later.occurred_at > acquired.occurred_at
                                  AND later.action IN ('released', 'expired'))
                 ) ranked
            WHERE ranked.rn = 1
        """)
        # project_member_permissions view — same join graph as the schema SSOT.
        conn.execute("""
            CREATE VIEW project_member_permissions AS
            SELECT pm.project_id, u.issuer AS user_issuer,
                   u.subject AS user_subject, p.permission_key,
                   pm.role_key, pm.assigned_at, pm.expires_at,
                   u.enabled AS user_enabled
            FROM project_membership pm
            JOIN users u ON u.id = pm.user_id
            JOIN roles r ON r.role_key = pm.role_key
            JOIN role_permissions rp ON rp.role_id = r.id
            JOIN permissions p ON p.id = rp.permission_id
        """)
        # Seed role catalog from the SSOT loader so the join resolves grants.
        permission_ids: dict[str, str] = {}
        all_permissions: set[str] = set()
        for role_key in PROJECT_ROLE_KEYS:
            all_permissions.update(permissions_for(role_key))
        for permission_key in sorted(all_permissions):
            pid = str(uuid.uuid4())
            permission_ids[permission_key] = pid
            conn.execute(
                'INSERT INTO permissions (id, permission_key) VALUES (?, ?)',
                (pid, permission_key),
            )
        role_ids: dict[str, str] = {}
        for role_key in sorted(PROJECT_ROLE_KEYS):
            rid = str(uuid.uuid4())
            role_ids[role_key] = rid
            conn.execute('INSERT INTO roles (id, role_key) VALUES (?, ?)', (rid, role_key))
            for permission_key in permissions_for(role_key):
                conn.execute(
                    'INSERT INTO role_permissions (role_id, permission_id) VALUES (?, ?)',
                    (rid, permission_ids[permission_key]),
                )
        conn.commit()


def _insert_user(
    db_path: str,
    user_id: str,
    subject: str,
    *,
    issuer: str = 'urn:fcc:identity:legacy',
    enabled: int = 1,
) -> None:
    with SqliteConnectionContext(db_path) as conn:
        conn.execute(
            'INSERT INTO users (id, issuer, subject, enabled) VALUES (?, ?, ?, ?)',
            (user_id, issuer, subject, enabled),
        )
        conn.commit()


def _insert_membership(
    db_path: str,
    *,
    project_id: str,
    user_id: str,
    role_key: str,
    expires_at: Optional[str] = None,
) -> None:
    with SqliteConnectionContext(db_path) as conn:
        conn.execute(
            'INSERT INTO project_membership '
            '(id, project_id, user_id, role_key, assigned_at, expires_at, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (
                str(uuid.uuid4()), project_id, user_id, role_key,
                '2026-05-28T00:00:00+00:00', expires_at,
                '2026-05-28T00:00:00+00:00',
            ),
        )
        conn.commit()


# ── 1. Role catalog SSOT ───────────────────────────────────────────────────


class TestRbacRoleCatalogSsot(unittest.TestCase):
    """The role catalog is a pure projection of the schema JSON — no code grants."""

    def test_known_role_set_matches_schema(self):
        # Schema declares 4 roles (Phase D added project_pm); loader exposes the
        # exact set verbatim from rbac_role_grants.
        self.assertEqual(PROJECT_ROLE_KEYS, frozenset({
            'project_viewer', 'project_engineer', 'project_admin', 'project_pm',
        }))

    def test_permissions_widen_monotonically(self):
        viewer = permissions_for('project_viewer')
        engineer = permissions_for('project_engineer')
        admin = permissions_for('project_admin')
        self.assertTrue(viewer.issubset(engineer))
        self.assertTrue(engineer.issubset(admin))
        # admin strictly includes platform:admin which others do not.
        self.assertIn('platform:admin', admin)
        self.assertNotIn('platform:admin', engineer)

    def test_unknown_role_returns_empty(self):
        self.assertFalse(is_known_role('not-a-real-role'))
        self.assertFalse(is_known_role(None))
        self.assertEqual(permissions_for('not-a-real-role'), frozenset())

    def test_no_role_or_permission_literal_in_application_code(self):
        # AST guard — the only place role_key / permission_key literals are
        # allowed in src/application/platform/ is the rbac_role_catalog loader
        # (which reads them FROM the schema). A drift back into hardcoded
        # grants would re-introduce the very SSOT violation FE-P8 closes.
        roots = _platform_application_roots()
        offenders: list[str] = []
        for root in roots:
            for path in root.rglob('*.py'):
                if path.name == 'rbac_role_catalog.py':
                    continue  # the only legal reader of the JSON
                source = path.read_text(encoding='utf-8')
                # Heuristic: a Python dict literal like ``"project_viewer": [`` or
                # ``'project_engineer': [`` in application code would be a grant
                # declaration. The schema-derived role_key tokens MUST NOT appear
                # next to a list/tuple literal in code outside the catalog.
                for role_key in PROJECT_ROLE_KEYS:
                    if (f'"{role_key}":' in source or f"'{role_key}':" in source) and (
                        '[' in source.split(role_key, 1)[1][:64]
                    ):
                        offenders.append(f'{path.name}: hardcoded grant for {role_key!r}')
        self.assertEqual(offenders, [], offenders)


class TestTheAuditVerbMatcherHasTeeth(unittest.TestCase):
    """⚠️ **매처가 «일하는지»를 매처에게 직접 묻는다** (2026-09-05).

    `test_audit_events_table_append_only_in_application_paths` 는 실제 소스를 훑어
    「offenders 가 공집합인가」를 본다. 그 형태는 **매처가 죽어도 초록**이다 —
    빈 결과가 「위반 없음」과 구별되지 않기 때문이다. 실제로 그 매처는 대소문자
    불일치로 어떤 입력에도 매치되지 않는 채 오래 초록을 냈다.

    설계서 §7 이 적은 두 팔을 여기에 쓴다:

        첫째 팔  알려진 위반에서 **듣는가**      ← 공허해지면 이 팔이 빨개진다
        둘째 팔  정당한 append-only 에서 **안 듣는가**  ← 과잉 탐지를 막는다

    둘째가 없으면 「전부 위반」이라고 답하는 매처도 첫째 팔을 통과한다.
    """

    def test_the_matcher_fires_on_each_forbidden_verb(self):
        for verb, sql in (
            ('UPDATE "AUDIT_EVENTS"', 'UPDATE "audit_events" SET actor = %s'),
            ('DELETE FROM "AUDIT_EVENTS"', 'DELETE FROM "audit_events" WHERE id = %s'),
            ('MERGE INTO "AUDIT_EVENTS"', 'MERGE INTO "audit_events" USING src ON ...'),
            ('TRUNCATE "AUDIT_EVENTS"', 'TRUNCATE "audit_events"'),
        ):
            with self.subTest(verb=verb):
                source = f"def purge(conn):\n    conn.execute('{sql}')\n"
                self.assertIn(
                    verb, _forbidden_audit_verbs(source),
                    f'매처가 {verb} 를 놓쳤다 — 이 봉인은 공허하다')

    def test_the_matcher_is_silent_on_append_only_writes(self):
        source = (
            "def record(conn, row):\n"
            "    conn.execute('INSERT INTO \"audit_events\" (event_type) VALUES (%s)', row)\n"
        )
        self.assertEqual(
            [], _forbidden_audit_verbs(source),
            'append-only INSERT 를 위반으로 읽었다 — 과잉 탐지는 이 봉인을 끄게 만든다')

    def test_the_matcher_ignores_sources_that_never_mention_the_table(self):
        self.assertEqual([], _forbidden_audit_verbs('x = 1\n'))


class TestTheSealScopeSurvivesTheAdapterMove(unittest.TestCase):
    """⚠️ **넓힌 범위가 «실재»하는지 검사한다** (2026-09-05).

    이 파일의 세 AST 봉인은 디렉터리를 훑는다. 훑을 디렉터리가 없으면
    `Path.rglob()` 은 **예외 없이 빈 목록**을 주고, 「위반 0건」과 「아무것도 안
    봤다」가 같은 초록이 된다 — 설계서 §7 교훈 ③ 이 다루는 바로 그 값이다.

    S3 가 어댑터를 `infrastructure/adapters/driven/` 으로 옮기며 그 위험이 실물이
    됐다. 범위를 넓히는 것만으로는 부족하다: 넓힌 «대상»이 사라지거나 이름이 바뀌면
    스캔은 조용히 원래 크기로 돌아간다. 이 봉인이 그날 이름을 대며 멈춘다.

    ⚠️ 스캔한 파일이 «0개가 아님»만 보면 부족하다 — application 쪽 73파일이
    그 조건을 혼자 만족시켜 driven 이 통째로 빠져도 초록이 된다. 그래서 **driven
    자체가 말뭉치 안에 있는지**를 본다.
    """

    def test_the_driven_adapter_directory_is_in_scope(self):
        roots = _platform_application_roots()
        self.assertTrue(roots, '훑을 디렉터리가 하나도 없다 — 세 봉인이 공허하다')
        self.assertTrue(
            any(r.name == 'driven' for r in roots),
            f"S3 가 어댑터를 옮긴 `infrastructure/adapters/driven/` 이 스캔 범위에 "
            f"없다 — 그리로 옮겨진 코드의 role 리터럴·감사 동사 위반은 «조용히» "
            f"빠진다(두 검사 모두 `offenders == []` 형태라 파일이 빠지면 위반도 "
            f"함께 빠진다). 현재 범위: {[str(r) for r in roots]}")

    def test_the_scan_corpus_is_not_empty(self):
        files = list(_iter_scanned_sources())
        self.assertGreater(
            len(files), 0,
            '스캔 대상 *.py 가 0개다 — 세 봉인이 「위반 없음」을 「안 봤음」으로 '
            '바꿔치기하고 있다')


# ── 2. Authorize union (token + membership) ────────────────────────────────


class _AuthorizeFixture(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        self._tmp.close()
        self.db_path = self._tmp.name
        _build_central_fixture(self.db_path)
        self.factory = lambda: QmarkConnection(self.db_path)
        self.rbac_adapter = PostgresCentralRbacReadAdapter(self.factory)
        self.rbac_service = CentralRbacReadService(self.rbac_adapter)
        self.read_adapter = PostgresCentralReadAdapter(self.factory)
        self.read_service = CentralReadService(self.read_adapter)
        self.access_policy = ApiAccessPolicy(PLATFORM_API_OPERATIONS)

    def tearDown(self) -> None:
        Path(self.db_path).unlink(missing_ok=True)

    def _adapter(self, *, principal: Optional[ApiPrincipal]) -> PlatformApiAdapter:
        return PlatformApiAdapter(
            self.read_service,
            access_policy=self.access_policy,
            principal=principal,
            rbac_read_service=self.rbac_service,
        )


class TestAuthorizeUnion(_AuthorizeFixture):
    """Authorize allows when token OR membership grants the operation."""

    def test_token_path_allows_when_principal_carries_required_permission(self):
        principal = ApiPrincipal.from_permissions('alice', ['platform:read'])
        adapter = self._adapter(principal=principal)
        # No membership wired for alice; the token alone authorizes.
        adapter.authorize('get_project_coverage', project_id=_PROJECT_ID)

    def test_membership_path_allows_token_lacking_permission(self):
        # Bob has a project_engineer membership but no platform permissions in
        # his token — membership should grant platform:read + platform:claim.
        user_id = str(uuid.uuid4())
        _insert_user(self.db_path, user_id, 'bob@corp')
        _insert_membership(
            self.db_path, project_id=_PROJECT_ID, user_id=user_id,
            role_key='project_engineer',
        )
        principal = ApiPrincipal.from_permissions('bob@corp', [])
        adapter = self._adapter(principal=principal)
        adapter.authorize('get_project_coverage', project_id=_PROJECT_ID)
        adapter.authorize('acquire_project_claim', project_id=_PROJECT_ID)

    def test_membership_permission_is_scoped_by_issuer_and_subject(self):
        issuer_a = 'https://idp-a.example'
        issuer_b = 'https://idp-b.example'
        subject = 'shared-subject'
        user_a = str(uuid.uuid4())
        user_b = str(uuid.uuid4())
        _insert_user(self.db_path, user_a, subject, issuer=issuer_a)
        _insert_user(self.db_path, user_b, subject, issuer=issuer_b)
        _insert_membership(
            self.db_path, project_id=_PROJECT_ID, user_id=user_b,
            role_key='project_engineer',
        )

        adapter_a = self._adapter(
            principal=ApiPrincipal.from_permissions(subject, [], issuer=issuer_a),
        )
        adapter_b = self._adapter(
            principal=ApiPrincipal.from_permissions(subject, [], issuer=issuer_b),
        )

        with self.assertRaises(PlatformAuthorizationError):
            adapter_a.authorize('get_project_coverage', project_id=_PROJECT_ID)
        adapter_b.authorize('get_project_coverage', project_id=_PROJECT_ID)

    def test_exact_issuer_user_does_not_fall_back_to_legacy_membership(self):
        legacy_user = str(uuid.uuid4())
        exact_user = str(uuid.uuid4())
        _insert_user(self.db_path, legacy_user, 'same@corp')
        _insert_user(
            self.db_path, exact_user, 'same@corp',
            issuer='https://idp.example',
        )
        _insert_membership(
            self.db_path, project_id=_PROJECT_ID, user_id=legacy_user,
            role_key='project_engineer',
        )
        principal = ApiPrincipal.from_permissions(
            'same@corp', [], issuer='https://idp.example',
        )
        adapter = self._adapter(principal=principal)
        with self.assertRaises(PlatformAuthorizationError):
            adapter.authorize('get_project_coverage', project_id=_PROJECT_ID)

    def test_disabled_user_membership_does_not_grant(self):
        user_id = str(uuid.uuid4())
        _insert_user(self.db_path, user_id, 'disabled@corp', enabled=0)
        _insert_membership(
            self.db_path, project_id=_PROJECT_ID, user_id=user_id,
            role_key='project_engineer',
        )
        principal = ApiPrincipal.from_permissions('disabled@corp', [])
        adapter = self._adapter(principal=principal)
        with self.assertRaises(PlatformAuthorizationError):
            adapter.authorize('get_project_coverage', project_id=_PROJECT_ID)

    def test_disabled_user_token_permission_does_not_grant(self):
        _insert_user(self.db_path, str(uuid.uuid4()), 'disabled@corp', enabled=0)
        principal = ApiPrincipal.from_permissions('disabled@corp', ['platform:read'])
        adapter = self._adapter(principal=principal)
        with self.assertRaises(PlatformAuthorizationError):
            adapter.authorize('get_project_coverage', project_id=_PROJECT_ID)

    def test_membership_denies_op_outside_role_grants(self):
        # A viewer (read-only) cannot claim or admin.
        user_id = str(uuid.uuid4())
        _insert_user(self.db_path, user_id, 'cathy@corp')
        _insert_membership(
            self.db_path, project_id=_PROJECT_ID, user_id=user_id,
            role_key='project_viewer',
        )
        principal = ApiPrincipal.from_permissions('cathy@corp', [])
        adapter = self._adapter(principal=principal)
        # Read passes through membership.
        adapter.authorize('get_project_coverage', project_id=_PROJECT_ID)
        # Claim is denied — viewer doesn't grant platform:claim.
        with self.assertRaises(PlatformAuthorizationError):
            adapter.authorize('acquire_project_claim', project_id=_PROJECT_ID)
        with self.assertRaises(PlatformAuthorizationError):
            adapter.authorize('assign_project_membership', project_id=_PROJECT_ID)

    def test_expired_membership_does_not_grant(self):
        # An assignment whose expires_at is in the past must NOT authorize.
        user_id = str(uuid.uuid4())
        _insert_user(self.db_path, user_id, 'doris@corp')
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        _insert_membership(
            self.db_path, project_id=_PROJECT_ID, user_id=user_id,
            role_key='project_engineer', expires_at=past,
        )
        principal = ApiPrincipal.from_permissions('doris@corp', [])
        adapter = self._adapter(principal=principal)
        with self.assertRaises(PlatformAuthorizationError):
            adapter.authorize('get_project_coverage', project_id=_PROJECT_ID)

    def test_membership_scoped_to_project(self):
        # A membership in project A does NOT authorize requests for project B.
        user_id = str(uuid.uuid4())
        _insert_user(self.db_path, user_id, 'erin@corp')
        _insert_membership(
            self.db_path, project_id=_PROJECT_ID, user_id=user_id,
            role_key='project_engineer',
        )
        principal = ApiPrincipal.from_permissions('erin@corp', [])
        adapter = self._adapter(principal=principal)
        adapter.authorize('get_project_coverage', project_id=_PROJECT_ID)
        with self.assertRaises(PlatformAuthorizationError):
            adapter.authorize('get_project_coverage', project_id=_OTHER_PROJECT_ID)

    def test_anonymous_principal_denied_even_with_rbac_wired(self):
        # An anonymous principal must NOT pick up grants from "anonymous"
        # appearing in the project_membership table (an attacker-controlled
        # subject literal cannot escalate).
        principal = ApiPrincipal.anonymous()
        adapter = self._adapter(principal=principal)
        with self.assertRaises(PlatformAuthorizationError):
            adapter.authorize('get_project_coverage', project_id=_PROJECT_ID)

    def test_no_rbac_wired_falls_back_to_token_only_path(self):
        # Backward compat: without rbac_read_service, only the token path
        # decides (identical to FE-P0d behaviour).
        principal = ApiPrincipal.from_permissions('alice', ['platform:read'])
        adapter = PlatformApiAdapter(
            self.read_service,
            access_policy=self.access_policy,
            principal=principal,
        )  # rbac_read_service=None
        adapter.authorize('get_project_coverage', project_id=_PROJECT_ID)
        with self.assertRaises(PlatformAuthorizationError):
            adapter.authorize(
                'acquire_project_claim', project_id=_PROJECT_ID,
            )


# ── 3. Operator provenance ─────────────────────────────────────────────────


class TestOperatorProvenance(_AuthorizeFixture):
    """Claim acquire/release operator MUST derive from the authenticated principal."""

    def _wire_claim(self, principal: ApiPrincipal) -> tuple[PlatformApiAdapter, str]:
        # principal must carry platform:claim so the authorize path passes.
        audit_writer = PostgresCentralAuditWriteAdapter()
        claim_adapter = PostgresCentralClaimWriteAdapter(self.factory, audit_writer=audit_writer)
        claim_service = ClaimWriteService(claim_adapter)
        adapter = PlatformApiAdapter(
            self.read_service,
            access_policy=self.access_policy,
            principal=principal,
            rbac_read_service=self.rbac_service,
            claim_write_service=claim_service,
        )
        return adapter, self.db_path

    def test_authenticated_principal_wins_over_body_operator(self):
        principal = ApiPrincipal.from_permissions('alice@corp', ['platform:claim'])
        adapter, db = self._wire_claim(principal)
        # Body claims operator='attacker' — must be ignored.
        envelope = adapter.acquire_project_claim(_PROJECT_ID, {
            'technology': 'BT',
            'condition_hash': 'h1',
            'operator': 'attacker@evil',
        })
        self.assertEqual(envelope['operator'], 'alice@corp')
        # Audit row's actor is the same principal subject.
        audit_rows = _read_audit_rows(db)
        self.assertEqual(len(audit_rows), 1)
        self.assertEqual(audit_rows[0]['actor_subject'], 'alice@corp')
        self.assertEqual(audit_rows[0]['event_type'], 'claim.acquired')

    def test_anonymous_falls_back_to_body_operator(self):
        # With auth disabled (anonymous principal), the body value is honored
        # — local-dev compatibility. The test confirms backward compat.
        principal = ApiPrincipal.anonymous()
        # Bypass authorize for this test by handing platform:claim via token —
        # this lets us exercise the operator-resolution branch independently.
        principal = ApiPrincipal.from_permissions('anonymous', ['platform:claim'])
        adapter, db = self._wire_claim(principal)
        envelope = adapter.acquire_project_claim(_PROJECT_ID, {
            'technology': 'BT',
            'condition_hash': 'h2',
            'operator': 'local-dev-user',
        })
        # Anonymous-or-empty actor → body operator wins.
        self.assertEqual(envelope['operator'], 'local-dev-user')


# ── 4. Audit log atomicity ─────────────────────────────────────────────────


class TestAuditAtomicity(_AuthorizeFixture):
    """Audit row + primary write share one transaction."""

    def test_acquire_writes_audit_row_in_same_transaction(self):
        principal = ApiPrincipal.from_permissions('alice@corp', ['platform:claim'])
        audit_writer = PostgresCentralAuditWriteAdapter()
        claim_adapter = PostgresCentralClaimWriteAdapter(self.factory, audit_writer=audit_writer)
        claim_service = ClaimWriteService(claim_adapter)
        adapter = PlatformApiAdapter(
            self.read_service, access_policy=self.access_policy, principal=principal,
            rbac_read_service=self.rbac_service, claim_write_service=claim_service,
        )
        adapter.acquire_project_claim(_PROJECT_ID, {
            'technology': 'BT', 'condition_hash': 'hX',
        })
        claim_rows = _read_claim_rows(self.db_path)
        audit_rows = _read_audit_rows(self.db_path)
        self.assertEqual(len(claim_rows), 1)
        self.assertEqual(len(audit_rows), 1)
        # The audit row's target_claim_id matches the inserted claim's claim_id.
        self.assertEqual(audit_rows[0]['target_claim_id'], claim_rows[0]['claim_id'])

    def test_failed_audit_rolls_primary_back(self):
        """A simulated audit failure must roll the claim INSERT back."""
        principal = ApiPrincipal.from_permissions('alice@corp', ['platform:claim'])

        # Drop audit_events table so the audit INSERT fails — the entire
        # transaction (claim + audit) must roll back. Explicit close() —
        # ``with sqlite3.connect(...) as conn`` is a transaction CM (not a
        # connection one) so a Windows file handle would otherwise leak past
        # the block and trip ``Path.unlink`` in tearDown.
        with SqliteConnectionContext(self.db_path) as drop_conn:
            drop_conn.execute('DROP TABLE audit_events')
            drop_conn.commit()

        audit_writer = PostgresCentralAuditWriteAdapter()
        claim_adapter = PostgresCentralClaimWriteAdapter(self.factory, audit_writer=audit_writer)
        claim_service = ClaimWriteService(claim_adapter)
        adapter = PlatformApiAdapter(
            self.read_service, access_policy=self.access_policy, principal=principal,
            rbac_read_service=self.rbac_service, claim_write_service=claim_service,
        )
        # Claim must fail loudly (the wrapped ClaimWriteError is the contract).
        from fcc_test_platform.domain.ports.output.central_claim_write_port import ClaimWriteError
        with self.assertRaises(ClaimWriteError):
            adapter.acquire_project_claim(_PROJECT_ID, {
                'technology': 'BT', 'condition_hash': 'hY',
            })
        # Most importantly — the claim_events ledger MUST be empty (rollback).
        self.assertEqual(_read_claim_rows(self.db_path), [])

    def test_audit_events_table_append_only_in_application_paths(self):
        """AST guard — application/platform/ never emits UPDATE/DELETE/MERGE
        against audit_events."""
        offenders: list[str] = []
        for path in _iter_scanned_sources():
            for verb in _forbidden_audit_verbs(path.read_text(encoding='utf-8')):
                offenders.append(f'{path.name}: forbidden {verb}')
        self.assertEqual(offenders, [])


def _read_claim_rows(db_path: str) -> list[dict]:
    with SqliteConnectionContext(db_path) as conn:
        cur = conn.execute(
            'SELECT claim_id, action, operator, condition_hash '
            'FROM claim_events ORDER BY occurred_at'
        )
        cols = ('claim_id', 'action', 'operator', 'condition_hash')
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _read_audit_rows(db_path: str) -> list[dict]:
    with SqliteConnectionContext(db_path) as conn:
        cols = AUDIT_EVENT_COLUMNS
        cur = conn.execute(
            f'SELECT {", ".join(cols)} FROM audit_events ORDER BY occurred_at'
        )
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── 5. Membership write service ────────────────────────────────────────────


class TestMembershipWriteService(_AuthorizeFixture):
    def setUp(self) -> None:
        super().setUp()
        self.audit_writer = PostgresCentralAuditWriteAdapter()
        self.membership_adapter = PostgresCentralMembershipWriteAdapter(
            self.factory, audit_writer=self.audit_writer,
        )
        self.membership_service = MembershipWriteService(
            self.membership_adapter, self.rbac_service,
        )

    def test_assign_unknown_role_raises_400(self):
        _insert_user(self.db_path, str(uuid.uuid4()), 'alice@corp')
        with self.assertRaises(MembershipRoleUnknownError):
            self.membership_service.assign(
                _PROJECT_ID,
                user_issuer='urn:fcc:identity:legacy',
                user_subject='alice@corp',
                role_key='not-a-real-role',
                actor_subject='admin@corp',
            )
        # MembershipRoleUnknownError is a ValueError → 400.
        try:
            self.membership_service.assign(
                _PROJECT_ID,
                user_issuer='urn:fcc:identity:legacy',
                user_subject='alice@corp',
                role_key='whatever',
                actor_subject='admin@corp',
            )
        except MembershipRoleUnknownError as exc:
            self.assertEqual(api_error_status(exc), 400)

    def test_assign_unknown_user_raises_404(self):
        # No central users row for "ghost@corp" before login/JIT provisioning:
        # assignment still fails closed. Once the user authenticates through the
        # platform surface, JIT creates the row and this same assignment path can
        # succeed without weakening the unknown-user gate.
        with self.assertRaises(MembershipUserUnknownError) as ctx:
            self.membership_service.assign(
                _PROJECT_ID,
                user_issuer='urn:fcc:identity:legacy',
                user_subject='ghost@corp',
                role_key='project_viewer',
                actor_subject='admin@corp',
            )
        self.assertEqual(api_error_status(ctx.exception), 404)
        self.assertEqual(_read_users(self.db_path), [])

        _insert_user(self.db_path, str(uuid.uuid4()), 'ghost@corp')
        envelope = self.membership_service.assign(
            _PROJECT_ID,
            user_issuer='urn:fcc:identity:legacy',
            user_subject='ghost@corp',
            role_key='project_viewer',
            actor_subject='admin@corp',
        )
        self.assertEqual(envelope['user_subject'], 'ghost@corp')

    def test_assign_resolves_user_by_issuer_and_subject(self):
        issuer_a = 'https://idp-a.example'
        issuer_b = 'https://idp-b.example'
        subject = 'shared-subject'
        user_a = str(uuid.uuid4())
        user_b = str(uuid.uuid4())
        _insert_user(self.db_path, user_a, subject, issuer=issuer_a)
        _insert_user(self.db_path, user_b, subject, issuer=issuer_b)

        envelope = self.membership_service.assign(
            _PROJECT_ID,
            user_issuer=issuer_b,
            user_subject=subject,
            role_key='project_viewer',
            actor_subject='admin@corp',
        )

        self.assertEqual(envelope['user_issuer'], issuer_b)
        self.assertEqual(envelope['user_subject'], subject)
        self.assertEqual(_read_memberships(self.db_path)[0]['user_id'], user_b)

    def test_assign_blank_issuer_defaults_to_legacy(self):
        # A blank user_issuer canonicalizes to the legacy issuer (consistent with
        # create_project) so the subject-based UI can assign without knowing the
        # issuer URL; the target user is resolved under the legacy issuer.
        _insert_user(self.db_path, str(uuid.uuid4()), 'alice@corp')
        envelope = self.membership_service.assign(
            _PROJECT_ID,
            user_subject='alice@corp',
            role_key='project_engineer',
            actor_subject='admin@corp',
        )
        self.assertEqual(envelope['user_issuer'], 'urn:fcc:identity:legacy')
        self.assertEqual(envelope['user_subject'], 'alice@corp')

    def test_assign_writes_membership_and_audit_atomically(self):
        _insert_user(self.db_path, str(uuid.uuid4()), 'alice@corp')
        envelope = self.membership_service.assign(
            _PROJECT_ID,
            user_issuer='urn:fcc:identity:legacy',
            user_subject='alice@corp',
            role_key='project_engineer',
            actor_subject='admin@corp',
        )
        self.assertEqual(envelope['user_subject'], 'alice@corp')
        self.assertEqual(envelope['role_key'], 'project_engineer')
        audit_rows = _read_audit_rows(self.db_path)
        self.assertEqual(len(audit_rows), 1)
        self.assertEqual(audit_rows[0]['event_type'], 'membership.assigned')
        self.assertEqual(audit_rows[0]['actor_subject'], 'admin@corp')
        self.assertEqual(audit_rows[0]['target_user_subject'], 'alice@corp')
        self.assertEqual(audit_rows[0]['role_key'], 'project_engineer')

    def test_assign_is_idempotent_upsert(self):
        # Re-assigning the same (project, user, role) is a no-op upsert that
        # still audits the operation (each assign call is an audited action,
        # even if the final state is identical).
        _insert_user(self.db_path, str(uuid.uuid4()), 'alice@corp')
        self.membership_service.assign(
            _PROJECT_ID,
            user_issuer='urn:fcc:identity:legacy',
            user_subject='alice@corp',
            role_key='project_engineer',
            actor_subject='admin@corp',
        )
        self.membership_service.assign(
            _PROJECT_ID,
            user_issuer='urn:fcc:identity:legacy',
            user_subject='alice@corp',
            role_key='project_engineer',
            actor_subject='admin@corp',
        )
        membership_rows = _read_memberships(self.db_path)
        self.assertEqual(len(membership_rows), 1)  # UPSERT — one row, two audits.
        self.assertEqual(len(_read_audit_rows(self.db_path)), 2)

    def test_revoke_not_assigned_raises_404(self):
        _insert_user(self.db_path, str(uuid.uuid4()), 'alice@corp')
        with self.assertRaises(MembershipNotFoundError) as ctx:
            self.membership_service.revoke(
                _PROJECT_ID,
                user_issuer='urn:fcc:identity:legacy',
                user_subject='alice@corp',
                role_key='project_viewer',
                actor_subject='admin@corp',
            )
        self.assertEqual(api_error_status(ctx.exception), 404)
        # No audit row written for a not-found revoke (don't audit a no-op).
        self.assertEqual(_read_audit_rows(self.db_path), [])

    def test_revoke_deletes_and_audits_atomically(self):
        user_id = str(uuid.uuid4())
        _insert_user(self.db_path, user_id, 'alice@corp')
        _insert_membership(
            self.db_path, project_id=_PROJECT_ID, user_id=user_id,
            role_key='project_engineer',
        )
        envelope = self.membership_service.revoke(
            _PROJECT_ID,
            user_issuer='urn:fcc:identity:legacy',
            user_subject='alice@corp',
            role_key='project_engineer',
            actor_subject='admin@corp',
        )
        self.assertEqual(envelope['user_subject'], 'alice@corp')
        self.assertEqual(_read_memberships(self.db_path), [])
        audit_rows = _read_audit_rows(self.db_path)
        self.assertEqual(len(audit_rows), 1)
        self.assertEqual(audit_rows[0]['event_type'], 'membership.revoked')


def _read_memberships(db_path: str) -> list[dict]:
    with SqliteConnectionContext(db_path) as conn:
        cur = conn.execute(
            'SELECT project_id, user_id, role_key FROM project_membership'
        )
        cols = ('project_id', 'user_id', 'role_key')
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _read_users(db_path: str) -> list[dict]:
    with SqliteConnectionContext(db_path) as conn:
        cur = conn.execute('SELECT id, issuer, subject, enabled FROM users ORDER BY id')
        cols = ('id', 'issuer', 'subject', 'enabled')
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── 6. G1 no-op audit grain policy structural guard ───────────────────────


def _class_method_body(dotted: str, class_name: str, method_name: str) -> list[ast.stmt]:
    # ⚠️ **경로가 아니라 모듈에게 묻는다** (2026-09-03). 추출(2026-08-30)이 이
    # 모듈을 `fcc_test_platform/` 아래로 옮겼고 `src/application/platform/` 은
    # 아무 트리에도 없다. `moved_module_source` 는 없는 모듈에 예외를 내므로
    # 이 지도가 다시 낡으면 조용히 통과하지 않는다.
    tree = ast.parse(moved_module_source(dotted).read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    return item.body
    raise AssertionError(f'{class_name}.{method_name} not found')


def _nested_function_body(statements: list[ast.stmt], name: str) -> list[ast.stmt]:
    for statement in statements:
        if isinstance(statement, ast.FunctionDef) and statement.name == name:
            return statement.body
    raise AssertionError(f'nested function {name} not found')


def _statement_index(
    statements: list[ast.stmt],
    predicate,
    *,
    label: str,
) -> int:
    for index, statement in enumerate(statements):
        if predicate(statement):
            return index
    raise AssertionError(f'{label} not found')


def _calls_attr(node: ast.AST, attr_name: str) -> bool:
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == attr_name
        ):
            return True
    return False


def _execute_uses_sql_name(node: ast.AST, sql_name: str) -> bool:
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == 'execute'
            and child.args
            and isinstance(child.args[0], ast.Name)
            and child.args[0].id == sql_name
        ):
            return True
    return False


class TestNoOpAuditGrainPolicyStructuralGuard(unittest.TestCase):
    """ADR-0014 G1: no-op audit policy is per-resource and explicitly sealed."""

    def test_claim_acquire_contention_is_delta_grained_before_audit(self):
        body = _nested_function_body(
            _class_method_body(
                'fcc_test_platform.application.central_claim_write_adapter',
                'PostgresCentralClaimWriteAdapter',
                'acquire_claim_if_unclaimed',
            ),
            '_txn',
        )
        conflict_index = _statement_index(
            body,
            lambda node: isinstance(node, ast.If)
            and any(
                isinstance(child, ast.Name) and child.id == 'existing'
                for child in ast.walk(node.test)
            ),
            label='existing-claim no-op branch',
        )
        insert_index = _statement_index(
            body,
            lambda node: _execute_uses_sql_name(node, 'INSERT_CLAIM_EVENT_SQL'),
            label='claim INSERT',
        )
        audit_index = _statement_index(
            body,
            lambda node: _calls_attr(node, '_maybe_audit'),
            label='claim audit call',
        )
        conflict_branch = body[conflict_index]
        self.assertIsInstance(conflict_branch, ast.If)
        self.assertTrue(any(isinstance(node, ast.Return) for node in conflict_branch.body))
        self.assertFalse(_calls_attr(conflict_branch, '_maybe_audit'))
        self.assertLess(conflict_index, insert_index)
        self.assertLess(insert_index, audit_index)

    def test_membership_assign_is_action_grained_audit_after_upsert(self):
        body = _nested_function_body(
            _class_method_body(
                'fcc_test_platform.application.central_membership_write_adapter',
                'PostgresCentralMembershipWriteAdapter',
                'assign_role_with_audit',
            ),
            '_txn',
        )
        upsert_index = _statement_index(
            body,
            lambda node: _execute_uses_sql_name(node, 'UPSERT_MEMBERSHIP_SQL'),
            label='membership UPSERT',
        )
        audit_index = _statement_index(
            body,
            lambda node: _calls_attr(node, 'append_event_in_transaction'),
            label='membership assign audit call',
        )
        select_index = _statement_index(
            body,
            lambda node: _execute_uses_sql_name(node, 'SELECT_MEMBERSHIP_WITH_SUBJECT_SQL'),
            label='membership read-back SELECT',
        )
        first_if_index = _statement_index(
            body,
            lambda node: isinstance(node, ast.If),
            label='post-UPSERT consistency guard',
        )
        self.assertLess(upsert_index, audit_index)
        self.assertLess(audit_index, select_index)
        self.assertLess(select_index, first_if_index)

    def test_membership_revoke_missing_role_is_delta_grained_before_audit(self):
        body = _nested_function_body(
            _class_method_body(
                'fcc_test_platform.application.central_membership_write_adapter',
                'PostgresCentralMembershipWriteAdapter',
                'revoke_role_with_audit',
            ),
            '_txn',
        )
        missing_index = _statement_index(
            body,
            lambda node: isinstance(node, ast.If)
            and any(
                isinstance(child, ast.Name) and child.id == 'rows'
                for child in ast.walk(node.test)
            ),
            label='missing-membership no-op branch',
        )
        delete_index = _statement_index(
            body,
            lambda node: _execute_uses_sql_name(node, 'DELETE_MEMBERSHIP_SQL'),
            label='membership DELETE',
        )
        audit_index = _statement_index(
            body,
            lambda node: _calls_attr(node, 'append_event_in_transaction'),
            label='membership revoke audit call',
        )
        missing_branch = body[missing_index]
        self.assertIsInstance(missing_branch, ast.If)
        self.assertTrue(any(isinstance(node, ast.Return) for node in missing_branch.body))
        self.assertFalse(_calls_attr(missing_branch, 'append_event_in_transaction'))
        self.assertLess(missing_index, delete_index)
        self.assertLess(delete_index, audit_index)


# ── 7. Adapter-layer purity (frozen-exe safe) ──────────────────────────────


class TestRbacAdapterPurity(unittest.TestCase):
    """The new RBAC + membership + audit modules must not import a PostgreSQL
    driver at module level (frozen-exe safety, same discipline as
    CentralReadAdapter / CentralClaimWriteAdapter)."""

    def test_no_psycopg_module_level_import(self):
        forbidden = ('psycopg', 'psycopg2', 'sqlalchemy', 'fastapi', 'pyvisa', 'PySide6')
        # ⚠️ **경로가 아니라 모듈에게 묻는다** (2026-09-03) — 여섯 전부.
        # 앞선 정정이 둘만 모듈 이름으로 바꾸고 넷을 경로로 남겨, 한 목록 안에
        # **두 축이 섞였다.** 섞인 목록은 다음 사람이 어느 쪽이 맞는지 알 수 없다.
        modules = (
            'fcc_test_platform.application.central_rbac_read_adapter',
            'fcc_test_platform.application.central_rbac_read_service',
            'fcc_test_platform.application.central_audit_write_adapter',
            'fcc_test_platform.application.central_membership_write_adapter',
            'fcc_test_platform.application.central_membership_write_service',
            'fcc_test_platform.application.rbac_role_catalog',
        )
        self.assertTrue(modules, '검사할 모듈이 0개다 — 이 검사는 아무것도 판정하지 않는다.')
        for module_path in modules:
            tree = ast.parse(moved_module_source(module_path).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for token in forbidden:
                            self.assertNotIn(
                                token, alias.name.split('.')[0],
                                f'{module_path} imports forbidden {alias.name}',
                            )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    head = node.module.split('.')[0]
                    self.assertNotIn(
                        head, forbidden,
                        f'{module_path} imports from forbidden {node.module}',
                    )


# ── 8. Audit event_type taxonomy alignment ─────────────────────────────────


class TestAuditEventTypeAlignment(unittest.TestCase):
    """Every audit event_type the application emits must be in the schema
    allowed_values; every allowed_value must be emitted by some application
    path. Drift between the two surfaces a typo or an orphaned token."""

    def test_application_event_types_are_subset_of_schema(self):
        schema = json.loads(
            (project_root / 'docs' / 'platform' / 'central_db_schema.v1.json')
            .read_text(encoding='utf-8')
        )
        allowed = set(schema['tables']['audit_events']['columns']['event_type']['allowed_values'])
        # Application emits: claim service (acquired/released/expired) +
        # membership service (assigned/revoked). The grep scope is the
        # platform application package.
        emitted: set[str] = set()
        for path in _iter_scanned_sources():
            source = path.read_text(encoding='utf-8')
            for token in allowed:
                if f"'{token}'" in source or f'"{token}"' in source:
                    emitted.add(token)
        # Every emitted token must be in allowed (cross-source guard).
        self.assertTrue(emitted.issubset(allowed), emitted - allowed)
        # Every allowed token MUST be emitted somewhere — no orphan.
        self.assertEqual(emitted, allowed, allowed - emitted)


class TestMembershipIssuerResolution(unittest.TestCase):
    """Membership must find users provisioned under their real OIDC issuer.

    Regression origin: central identity is keyed by ``(issuer, subject)``. JIT
    provisioning writes the row under the VALIDATED OIDC issuer, but membership
    assign canonicalized a blank issuer straight to the legacy issuer — so
    granting a role to any OIDC user 404'd, from the UI too (the frontend sends
    no ``user_issuer``). A live DB showed the same subject split across both
    issuers as a result.
    """

    REAL_ISSUER = 'http://central.example:8081/realms/fcc-dev'
    SUBJECT = 'b9767006-ce3b-4b0f-ad69-0431cae39c7c'
    PROJECT_ID = '4f4e6500-8a6c-4bc9-b7fe-bfb6915ab8fb'

    def _service(self, rows):
        from fcc_test_platform.application.central_membership_write_service import (
            MembershipWriteService,
        )

        tried: list[str] = []

        class _Rbac:
            def resolve_user_id(self, subject, *, user_issuer):
                tried.append(user_issuer)
                return rows.get((user_issuer, subject), '')

        class _Write:
            def assign_role_with_audit(self, membership, _audit):
                return {
                    'project_id': membership['project_id'],
                    'user_subject': TestMembershipIssuerResolution.SUBJECT,
                    'role_key': membership['role_key'],
                    'team': membership['team'],
                    'assigned_at': membership['assigned_at'],
                    'expires_at': membership['expires_at'],
                }

        return MembershipWriteService(_Write(), _Rbac()), tried

    def test_blank_issuer_resolves_against_the_actor_issuer_first(self):
        from fcc_test_contracts.common.identity import LEGACY_IDENTITY_ISSUER
        from fcc_test_platform.application.central_membership_write_service import (
            issuer_lookup_candidates,
        )

        self.assertEqual(
            issuer_lookup_candidates('', self.REAL_ISSUER),
            (self.REAL_ISSUER, LEGACY_IDENTITY_ISSUER),
        )

    def test_explicit_issuer_is_never_widened(self):
        from fcc_test_platform.application.central_membership_write_service import (
            issuer_lookup_candidates,
        )

        self.assertEqual(
            issuer_lookup_candidates('http://other/realms/x', self.REAL_ISSUER),
            ('http://other/realms/x',),
        )

    def test_assign_finds_an_oidc_user_without_an_explicit_issuer(self):
        service, tried = self._service({(self.REAL_ISSUER, self.SUBJECT): 'user-1'})

        envelope = service.assign(
            self.PROJECT_ID,
            user_subject=self.SUBJECT,
            role_key='project_engineer',
            actor_subject='admin',
            actor_issuer=self.REAL_ISSUER,
            team='RF',
        )

        self.assertEqual(envelope['role_key'], 'project_engineer')
        self.assertEqual(tried, [self.REAL_ISSUER])

    def test_legacy_rows_still_resolve_through_the_fallback(self):
        from fcc_test_contracts.common.identity import LEGACY_IDENTITY_ISSUER

        service, tried = self._service({(LEGACY_IDENTITY_ISSUER, self.SUBJECT): 'user-2'})

        service.assign(
            self.PROJECT_ID,
            user_subject=self.SUBJECT,
            role_key='project_pm',
            actor_subject='admin',
            actor_issuer=self.REAL_ISSUER,
        )

        self.assertEqual(tried, [self.REAL_ISSUER, LEGACY_IDENTITY_ISSUER])

    def test_unknown_subject_reports_every_issuer_it_tried(self):
        from fcc_test_platform.application.central_membership_write_service import (
            MembershipUserUnknownError,
        )

        service, _tried = self._service({})

        with self.assertRaises(MembershipUserUnknownError) as caught:
            service.assign(
                self.PROJECT_ID,
                user_subject=self.SUBJECT,
                role_key='project_engineer',
                actor_subject='admin',
                actor_issuer=self.REAL_ISSUER,
            )
        self.assertIn(self.REAL_ISSUER, str(caught.exception))

    def test_actor_issuer_comes_from_the_principal_never_the_body(self):
        """The boundary must read the issuer off the authenticated principal.

        A body-supplied ``actor_issuer`` would let a client point the lookup at
        any issuer, so the route helper takes it from ``self._principal`` only.
        """
        import ast
        from pathlib import Path

        routes = resolve_repo_artifact(__file__, 'src/infrastructure/adapters/driving/api/platform_routes.py')
        tree = ast.parse(routes.read_text(encoding='utf-8'))
        helper = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == '_actor_issuer'
        )
        # Inspect NODES, not an ast.dump string — the dump contains structural
        # keys ('body=[...]') and the docstring, so a substring scan would both
        # false-positive and silently pass on a rewrite.
        attributes = {
            node.attr for node in ast.walk(helper) if isinstance(node, ast.Attribute)
        }
        names = {node.id for node in ast.walk(helper) if isinstance(node, ast.Name)}
        self.assertIn('_principal', attributes)
        self.assertFalse(
            names & {'payload', 'body'},
            'the issuer must not come from client-controlled request data',
        )

        # Both membership writes must pass it, and pass the HELPER CALL — a
        # literal (e.g. actor_issuer='') would satisfy a name-only check while
        # reverting to the legacy-only lookup that caused the 404.
        for handler_name in ('assign_project_membership', 'revoke_project_membership'):
            handler = next(
                node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == handler_name
            )
            wired = [
                keyword.value
                for node in ast.walk(handler)
                if isinstance(node, ast.Call)
                for keyword in node.keywords
                if keyword.arg == 'actor_issuer'
            ]
            self.assertEqual(len(wired), 1, f'{handler_name}: expected one actor_issuer')
            value = wired[0]
            self.assertIsInstance(value, ast.Call, f'{handler_name}: must call the helper')
            self.assertIsInstance(value.func, ast.Attribute)
            self.assertEqual(value.func.attr, '_actor_issuer', handler_name)


if __name__ == '__main__':
    unittest.main()
