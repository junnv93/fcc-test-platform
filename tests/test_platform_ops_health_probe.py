"""Seals for the platform ops probes — liveness / readiness (2026-07-20).

The platform API is the only containerized surface with a hard external
dependency (the central PostgreSQL), and until this change it had **no** probe
at all: ``infra/docker-compose.central.yml`` had to borrow the auth-exempt
``/platform/metrics`` scrape endpoint as its healthcheck, which reports healthy
for a container that cannot reach the DB.

This module seals the four things that must not silently regress:

1. **Separation** — liveness is dependency-free (a failure means *restart me*),
   readiness consults the dependency (a failure means *stop routing to me*). If
   liveness ever started touching the DB, a DB blip would restart every replica.
2. **Readiness actually fails** — a broken dependency must produce 503, not a
   cheerful 200. This is the one property that makes the endpoint worth having,
   and the easiest to break by swallowing an exception.
3. **No disclosure** — both bodies are auth-exempt, so they must render only the
   fixed vocabulary (``ok`` / ``unavailable`` + a logical dependency name). A DSN,
   host, password or driver exception text must never appear.
4. **Probes are not throttled into failure** — an orchestrator reads 429 exactly
   like 503, so a probe that trips the rate limiter manufactures the restart it
   exists to prevent.

── Negative self-tests ────────────────────────────────────────────────────────
Every guard here has a paired ``*_negative_self_test`` that feeds the SAME
assertion helper a deliberately broken input and asserts it FAILS. A guard that
cannot fail is decoration; these prove each one bites.
"""

from __future__ import annotations

import re
import threading
import unittest
from pathlib import Path

import pytest

# Enrolled into the ``backend-invariants.yml`` CI gate (``-m invariant``). The
# marker is declared in-file rather than by extending the shared
# ``tests/conftest.py`` filename-token SSOT (another session owns that file).
pytestmark = pytest.mark.invariant

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = REPO_ROOT / 'infra' / 'docker-compose.central.yml'

from tests._moved_module_source import moved_module_source  # noqa: E402
from fcc_test_platform.application.central_health_adapter import (  # noqa: E402
    CENTRAL_PING_SQL,
    PostgresCentralHealthAdapter,
)
from fcc_test_platform.application.central_read_service import CentralReadService  # noqa: E402
from fcc_test_platform.application.readiness_service import ReadinessService  # noqa: E402
from domain.ports.output.central_read_port import CentralReadError  # noqa: E402
from fcc_test_contracts.common.health_probe_policy import (  # noqa: E402
    DEPENDENCY_CENTRAL_DB,
    LIVENESS_PATH_SUFFIX,
    PROBE_PATH_SUFFIXES,
    READINESS_CACHE_TTL_SECONDS,
    READINESS_DEPENDENCIES_KEY,
    READINESS_PATH_SUFFIX,
    READINESS_UNAVAILABLE_DETAIL,
    STATUS_KEY,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    ReadinessSnapshot,
    is_probe_path,
    liveness_payload,
)
from fcc_test_contracts.common.rate_limit_policy import (  # noqa: E402
    BUCKET_DEFAULT,
    BUCKET_METRICS,
    BUCKET_PROBE,
    DEFAULT_PROBE_MAX_REQUESTS,
    RateLimitPolicy,
    RateLimitRule,
)

from fcc_test_contracts.common.tree_artifacts import resolve_dependency_artifact


#: Tokens that must never appear in an auth-exempt probe body. Deliberately
#: includes values that WOULD appear if someone rendered the exception text of a
#: psycopg connection failure (which embeds host/user) or the DSN itself.
_DISCLOSURE_TOKENS = (
    'postgres', 'postgresql', 'psycopg', 'password', 'dsn', 'host=',
    '127.0.0.1', 'localhost', 'select 1', 'traceback', 'fcc_central',
)


def assert_no_disclosure(payload) -> None:
    """Fail when a rendered probe body carries anything but the safe vocabulary.

    Shared by the positive guards and by their negative self-tests, so the
    self-test exercises the *same* code path the real assertion uses.
    """
    text = repr(payload).lower()
    leaked = [token for token in _DISCLOSURE_TOKENS if token in text]
    if leaked:
        raise AssertionError(f'probe body leaked {leaked!r}: {payload!r}')


class _FakeCursor:
    def __init__(self, owner, fail: bool) -> None:
        self._owner = owner
        self._fail = fail
        self.rowcount = 0

    def execute(self, statement, parameters=()):  # noqa: D401 — DB-API shape
        self._owner.statements.append(statement)
        if self._fail:
            raise RuntimeError(
                'connection to server at "10.0.0.7", port 5432 failed: '
                'password authentication failed for user "fcc"'
            )

    def close(self) -> None:
        self._owner.cursors_closed += 1


class _FakeConnection:
    """Minimal DbConnection stand-in that records what the probe did."""

    def __init__(self, fail: bool = False) -> None:
        self.statements: list[str] = []
        self.cursors_closed = 0
        self.closed = 0
        self._fail = fail

    def cursor(self):
        return _FakeCursor(self, self._fail)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.closed += 1


class TestHealthProbePolicy(unittest.TestCase):
    """The pure domain vocabulary + freshness rule."""

    def test_liveness_payload_is_constant_and_dependency_free(self):
        self.assertEqual(liveness_payload(), {STATUS_KEY: STATUS_OK})
        # A caller mutating the payload must not poison later responses.
        payload = liveness_payload()
        payload['injected'] = True
        self.assertNotIn('injected', liveness_payload())

    def test_probe_path_matching_tolerates_slash_and_query(self):
        for path in (
            '/platform/health', '/platform/health/', '/platform/health?x=1',
            '/platform/ready', '/platform/ready/', '/session/health',
        ):
            self.assertTrue(is_probe_path(path), path)

    def test_non_probe_paths_are_not_probe_paths(self):
        for path in ('/platform/projects', '/platform/metrics', '/platform/healthy', ''):
            self.assertFalse(is_probe_path(path), path)

    def test_snapshot_ready_only_when_every_dependency_is_ok(self):
        ok = ReadinessSnapshot(dependencies={DEPENDENCY_CENTRAL_DB: STATUS_OK}, checked_at=0.0)
        bad = ReadinessSnapshot(
            dependencies={DEPENDENCY_CENTRAL_DB: STATUS_UNAVAILABLE}, checked_at=0.0,
        )
        self.assertTrue(ok.ready)
        self.assertFalse(bad.ready)
        # No declared dependency = nothing to check = ready.
        self.assertTrue(ReadinessSnapshot(dependencies={}, checked_at=0.0).ready)

    def test_snapshot_rejects_a_status_outside_the_vocabulary(self):
        with self.assertRaises(ValueError):
            ReadinessSnapshot(dependencies={DEPENDENCY_CENTRAL_DB: 'degraded'}, checked_at=0.0)

    def test_snapshot_freshness_window_and_backwards_clock(self):
        snapshot = ReadinessSnapshot(
            dependencies={DEPENDENCY_CENTRAL_DB: STATUS_OK}, checked_at=100.0,
        )
        self.assertTrue(snapshot.is_fresh(now=100.0, ttl=5.0))
        self.assertTrue(snapshot.is_fresh(now=104.999, ttl=5.0))
        self.assertFalse(snapshot.is_fresh(now=105.0, ttl=5.0))
        # A clock that stepped backwards must read as STALE, never fresh-forever.
        self.assertFalse(snapshot.is_fresh(now=99.0, ttl=5.0))

    def test_snapshot_is_immutable_against_the_source_mapping(self):
        source = {DEPENDENCY_CENTRAL_DB: STATUS_OK}
        snapshot = ReadinessSnapshot(dependencies=source, checked_at=0.0)
        source[DEPENDENCY_CENTRAL_DB] = STATUS_UNAVAILABLE
        self.assertTrue(snapshot.ready)

    def test_rendered_bodies_carry_no_disclosure(self):
        assert_no_disclosure(liveness_payload())
        assert_no_disclosure(
            ReadinessSnapshot(
                dependencies={DEPENDENCY_CENTRAL_DB: STATUS_UNAVAILABLE}, checked_at=0.0,
            ).as_dict()
        )
        self.assertEqual(READINESS_UNAVAILABLE_DETAIL, READINESS_UNAVAILABLE_DETAIL.strip())
        assert_no_disclosure(READINESS_UNAVAILABLE_DETAIL)

    def test_disclosure_guard_negative_self_test(self):
        """The disclosure helper must reject a body that DOES leak."""
        with self.assertRaises(AssertionError):
            assert_no_disclosure({
                STATUS_KEY: STATUS_UNAVAILABLE,
                READINESS_DEPENDENCIES_KEY: {
                    DEPENDENCY_CENTRAL_DB: STATUS_UNAVAILABLE,
                },
                'error': 'could not connect to host=10.0.0.7 password authentication failed',
            })

    def test_readiness_body_shape(self):
        body = ReadinessSnapshot(
            dependencies={DEPENDENCY_CENTRAL_DB: STATUS_OK}, checked_at=1.0,
        ).as_dict()
        self.assertEqual(body[STATUS_KEY], STATUS_OK)
        self.assertEqual(body[READINESS_DEPENDENCIES_KEY], {DEPENDENCY_CENTRAL_DB: STATUS_OK})
        # ``checked_at`` is internal bookkeeping — it must not reach the wire
        # (it would expose process uptime characteristics for free).
        self.assertEqual(set(body), {STATUS_KEY, READINESS_DEPENDENCIES_KEY})


class TestCentralHealthAdapter(unittest.TestCase):
    """The dependency probe itself: cheap, closed, and loud on failure."""

    def test_ping_executes_the_cheapest_statement_and_closes_everything(self):
        connection = _FakeConnection()
        PostgresCentralHealthAdapter(lambda: connection).ping()
        self.assertEqual(connection.statements, [CENTRAL_PING_SQL])
        self.assertEqual(connection.cursors_closed, 1)
        self.assertEqual(connection.closed, 1)

    def test_ping_reads_no_table_or_view(self):
        """A probe that read a table would degrade with data volume/locks."""
        lowered = CENTRAL_PING_SQL.lower()
        for token in (' from ', 'join', 'coverage', 'pg_', 'information_schema'):
            self.assertNotIn(token, lowered)

    def test_connection_failure_is_wrapped_loudly(self):
        def _boom():
            raise RuntimeError('nope')

        with self.assertRaises(CentralReadError):
            PostgresCentralHealthAdapter(_boom).ping()

    def test_query_failure_is_wrapped_and_connection_still_closed(self):
        connection = _FakeConnection(fail=True)
        with self.assertRaises(CentralReadError):
            PostgresCentralHealthAdapter(lambda: connection).ping()
        self.assertEqual(connection.cursors_closed, 1)
        self.assertEqual(connection.closed, 1)


class TestReadinessService(unittest.TestCase):
    """TTL cache + single flight + failure translation."""

    def setUp(self):
        self.now = 1000.0
        self.calls = 0

    def _ok_probe(self):
        self.calls += 1

    def _failing_probe(self):
        self.calls += 1
        raise CentralReadError('connection to server at "10.0.0.7" failed: password ...')

    def _service(self, probe, ttl=READINESS_CACHE_TTL_SECONDS):
        return ReadinessService(
            {DEPENDENCY_CENTRAL_DB: probe}, clock=lambda: self.now, ttl=ttl,
        )

    def test_verdict_is_cached_for_the_ttl_then_refreshed(self):
        service = self._service(self._ok_probe, ttl=5.0)
        service.check()
        service.check()
        service.check()
        self.assertEqual(self.calls, 1, 'cache must bound the dependency touch rate')
        self.now += 5.0
        service.check()
        self.assertEqual(self.calls, 2, 'a stale verdict must be re-probed')

    def test_zero_ttl_disables_caching(self):
        service = self._service(self._ok_probe, ttl=0.0)
        service.check()
        service.check()
        self.assertEqual(self.calls, 2)

    def test_failing_dependency_yields_unavailable_not_an_exception(self):
        snapshot = self._service(self._failing_probe).check()
        self.assertFalse(snapshot.ready)
        self.assertEqual(
            snapshot.dependencies, {DEPENDENCY_CENTRAL_DB: STATUS_UNAVAILABLE},
        )
        assert_no_disclosure(snapshot.as_dict())

    def test_recovery_is_observed_after_the_ttl(self):
        state = {'fail': True}

        def _flaky():
            self.calls += 1
            if state['fail']:
                raise CentralReadError('down')

        service = self._service(_flaky, ttl=5.0)
        self.assertFalse(service.check().ready)
        state['fail'] = False
        self.assertFalse(service.check().ready, 'still cached inside the TTL')
        self.now += 5.0
        self.assertTrue(service.check().ready)

    def test_concurrent_cold_checks_probe_the_dependency_once(self):
        """Single flight: a herd on a cold cache must not fan out to the DB."""
        started = threading.Event()

        def _slow_probe():
            self.calls += 1
            started.set()
            # Long enough that the other threads are provably inside check().
            threading.Event().wait(0.05)

        service = self._service(_slow_probe, ttl=60.0)
        threads = [threading.Thread(target=service.check) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertTrue(started.is_set())
        self.assertEqual(self.calls, 1)

    def test_invalidate_forces_a_reprobe(self):
        service = self._service(self._ok_probe, ttl=60.0)
        service.check()
        service.invalidate()
        service.check()
        self.assertEqual(self.calls, 2)


class TestProbeRateLimitInteraction(unittest.TestCase):
    """Probes must not be throttled into a false 'unhealthy'."""

    def setUp(self):
        self.policy = RateLimitPolicy()

    def test_probe_paths_get_the_dedicated_probe_bucket(self):
        for path in ('/platform/health', '/platform/ready', '/session/health'):
            rule = self.policy.rule_for(path)
            self.assertEqual(rule.bucket, BUCKET_PROBE, path)

    def test_probe_bucket_is_disjoint_from_default_and_metrics(self):
        self.assertEqual(
            len({BUCKET_DEFAULT, BUCKET_METRICS, BUCKET_PROBE}), 3,
            'a shared bucket name would merge two budgets into one counter',
        )
        with self.assertRaises(ValueError):
            RateLimitPolicy(probe_rule=RateLimitRule(bucket=BUCKET_DEFAULT))

    def test_probe_budget_dwarfs_realistic_orchestrator_poll_rates(self):
        rule = self.policy.rule_for('/platform/ready')
        # compose polls every 15 s; a k8s liveness+readiness pair at 10 s adds
        # 12/min. 10x that combined rate is the floor we hold.
        combined_per_minute = 4 + 12
        self.assertGreaterEqual(rule.max_requests, combined_per_minute * 10)
        self.assertEqual(rule.max_requests, DEFAULT_PROBE_MAX_REQUESTS)

    def test_probe_budget_exceeds_the_default_api_budget(self):
        self.assertGreater(
            self.policy.rule_for('/platform/health').max_requests,
            self.policy.rule_for('/platform/projects').max_requests,
        )

    def test_probe_suffix_ssot_is_shared_with_the_policy(self):
        """The throttle must derive probe paths from the health policy SSOT."""
        # ⚠️ **경로가 아니라 모듈에게 묻는다** (2026-09-03). 이 모듈은 2026-08-31 에
        # 계약 레인으로 갔고, `src/domain/services/…` 는 이제 아무 트리에도 없다.
        # 경로를 하드코딩한 검사는 *트리*에 대해 단언하지 검사하려는 *코드*에 대해
        # 단언하지 않는다 — `tests/_moved_module_source.py` 참조.
        source = moved_module_source(
            'fcc_test_contracts.common.rate_limit_policy'
        ).read_text(encoding='utf-8')
        self.assertIn(
            'from fcc_test_contracts.common.health_probe_policy import', source)
        self.assertNotIn(f"'{LIVENESS_PATH_SUFFIX}'", source.replace("'/metrics'", ''))
        self.assertNotIn(f"'{READINESS_PATH_SUFFIX}'", source)

    def test_rule_selection_negative_self_test(self):
        """A non-probe path must NOT receive the probe budget."""
        self.assertNotEqual(
            self.policy.rule_for('/platform/projects').bucket, BUCKET_PROBE,
        )


class TestProbeEndpoints(unittest.TestCase):
    """End-to-end through the real FastAPI app (auth-exempt, real middleware)."""

    def _app(self, *, dependency_ok=True, rate_limit_policy=None):
        from fcc_test_platform.api.platform_routes import (
            PlatformApiAdapter,
            create_platform_app,
        )

        def _probe():
            if not dependency_ok:
                raise CentralReadError(
                    'connection to server at "10.0.0.7", port 5432 failed: '
                    'password authentication failed for user "fcc"'
                )

        adapter = PlatformApiAdapter(
            CentralReadService(object()),
            readiness_service=ReadinessService(
                {DEPENDENCY_CENTRAL_DB: _probe}, ttl=0.0,
            ),
        )
        return create_platform_app(adapter, rate_limit_policy=rate_limit_policy)

    def _client(self, **kwargs):
        from fastapi.testclient import TestClient
        return TestClient(self._app(**kwargs), raise_server_exceptions=False)

    def test_liveness_is_200_without_auth_and_without_the_dependency(self):
        client = self._client(dependency_ok=False)
        response = client.get(f'/platform{LIVENESS_PATH_SUFFIX}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {STATUS_KEY: STATUS_OK})
        assert_no_disclosure(response.json())

    def test_readiness_is_200_when_the_dependency_answers(self):
        response = self._client().get(f'/platform{READINESS_PATH_SUFFIX}')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body[STATUS_KEY], STATUS_OK)
        self.assertEqual(
            body[READINESS_DEPENDENCIES_KEY], {DEPENDENCY_CENTRAL_DB: STATUS_OK},
        )

    def test_readiness_actually_fails_when_the_dependency_is_down(self):
        response = self._client(dependency_ok=False).get(
            f'/platform{READINESS_PATH_SUFFIX}'
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.headers.get('content-type', '').split(';')[0],
            'application/problem+json',
            'not-ready must reuse the repository RFC 9457 error contract',
        )
        body = response.json()
        self.assertEqual(body['code'], 'UPSTREAM_UNAVAILABLE')
        self.assertEqual(body['detail'], READINESS_UNAVAILABLE_DETAIL)
        assert_no_disclosure(body)

    def test_probes_survive_a_flood_that_exhausts_the_default_api_budget(self):
        """The probe budget must be independent of (and larger than) the API one."""
        policy = RateLimitPolicy(
            default_rule=RateLimitRule(max_requests=2, window_seconds=60.0),
        )
        client = self._client(rate_limit_policy=policy)
        # Burn the default budget on a business path.
        for _ in range(5):
            client.get('/platform/projects')
        for _ in range(20):
            response = client.get(f'/platform{LIVENESS_PATH_SUFFIX}')
            self.assertEqual(response.status_code, 200, response.text)
            response = client.get(f'/platform{READINESS_PATH_SUFFIX}')
            self.assertNotEqual(response.status_code, 429, response.text)

    def test_probe_throttle_guard_negative_self_test(self):
        """With a tiny PROBE budget the same flood DOES 429 — the guard bites."""
        policy = RateLimitPolicy(
            probe_rule=RateLimitRule(
                max_requests=1, window_seconds=60.0, bucket=BUCKET_PROBE,
            ),
        )
        client = self._client(rate_limit_policy=policy)
        statuses = {
            client.get(f'/platform{LIVENESS_PATH_SUFFIX}').status_code
            for _ in range(30)
        }
        self.assertIn(429, statuses)

    def test_probes_are_absent_from_the_published_openapi_contract(self):
        """Infrastructure probes are not part of the business API artifact."""
        from fcc_test_kernel.application.central_contract.api_contracts import PLATFORM_API_ROUTES
        declared = {path for _, path in PLATFORM_API_ROUTES.values()}
        for suffix in PROBE_PATH_SUFFIXES:
            self.assertNotIn(f'/platform{suffix}', declared)


class TestComposeHealthcheckWiring(unittest.TestCase):
    """The compose healthcheck must actually use the readiness probe."""

    def setUp(self):
        self.text = COMPOSE_PATH.read_text(encoding='utf-8')
        # The platform-api service block, up to the next top-level service.
        match = re.search(
            r'\n  platform-api:\n(?P<body>(?:.*\n)*?)(?=\n  [a-z-]+:\n|\nvolumes:)',
            self.text,
        )
        self.assertIsNotNone(match, 'platform-api service block not found')
        self.body = match.group('body')

    def _healthcheck_command(self, body: str) -> str:
        match = re.search(r'healthcheck:\n(?:.*\n)*?\s+test:\n(?P<test>(?:.*\n)+?)\s+interval:', body)
        self.assertIsNotNone(match, 'platform-api healthcheck test not found')
        return match.group('test')

    def test_platform_api_healthcheck_probes_readiness(self):
        command = self._healthcheck_command(self.body)
        self.assertIn(f'/platform{READINESS_PATH_SUFFIX}', command)

    def test_platform_api_healthcheck_no_longer_borrows_metrics(self):
        command = self._healthcheck_command(self.body)
        self.assertNotIn('/platform/metrics', command)

    def test_healthcheck_guard_negative_self_test(self):
        """The extractor must reject a block whose healthcheck reverted."""
        reverted = self.body.replace(
            f'/platform{READINESS_PATH_SUFFIX}', '/platform/metrics',
        )
        command = self._healthcheck_command(reverted)
        self.assertNotIn(f'/platform{READINESS_PATH_SUFFIX}', command)

    def test_readiness_ttl_is_below_the_compose_poll_interval(self):
        """Consecutive orchestrator polls must never be served one cached verdict."""
        match = re.search(
            r'healthcheck:\n(?:.*\n)*?\s+interval:\s*(?P<seconds>\d+)s', self.body,
        )
        self.assertIsNotNone(match)
        self.assertLess(READINESS_CACHE_TTL_SECONDS, int(match.group('seconds')))


class TestHungDependencyCannotStallTheProcess(unittest.TestCase):
    """A hung dependency must pin ONE thread, not every concurrent prober.

    ``psycopg.connect`` is not time-bounded (the shared DSN carries no
    ``connect_timeout``), so a blackholed central DB blocks for ~130 s of TCP SYN
    retries. If ``check()`` queued on the single-flight lock, that would pin one
    server thread per concurrent readiness request; the endpoint is auth-exempt
    and polled by every orchestrator, so the FastAPI sync threadpool would be
    exhausted and the *business* API would stop serving — an availability defect
    caused by the very probe that reports availability.
    """

    def setUp(self):
        self.release = threading.Event()
        self.entered = threading.Event()
        self.probe_calls = 0

    def _hanging_probe(self):
        self.probe_calls += 1
        self.entered.set()
        # Stands in for a connect() against a blackholed host.
        self.release.wait(timeout=10)

    def _service(self, **kwargs):
        return ReadinessService({DEPENDENCY_CENTRAL_DB: self._hanging_probe}, **kwargs)

    def _with_probe_in_flight(self, service):
        """Start a probe, block it, and return the thread running it."""
        thread = threading.Thread(target=service.check, daemon=True)
        thread.start()
        self.assertTrue(self.entered.wait(timeout=5), 'probe never started')
        return thread

    def _drain(self, service, thread):
        self.release.set()
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())

    def test_cold_cache_contender_returns_immediately_and_fails_closed(self):
        service = self._service(ttl=60.0)
        thread = self._with_probe_in_flight(service)
        try:
            done = threading.Event()
            verdict = {}

            def _contend():
                verdict['snapshot'] = service.check()
                done.set()

            threading.Thread(target=_contend, daemon=True).start()
            self.assertTrue(
                done.wait(timeout=2),
                'a contended check() must not queue behind the hung probe',
            )
            snapshot = verdict['snapshot']
            self.assertFalse(
                snapshot.ready,
                'an unconfirmed dependency must fail closed (stop routing to me)',
            )
            self.assertEqual(
                snapshot.dependencies, {DEPENDENCY_CENTRAL_DB: STATUS_UNAVAILABLE},
            )
            assert_no_disclosure(snapshot.as_dict())
            self.assertEqual(self.probe_calls, 1, 'contender must not fan out a probe')
        finally:
            self._drain(service, thread)

    def test_a_hung_dependency_pins_exactly_one_thread(self):
        """The containment property itself: N contenders, N-1 return at once."""
        service = self._service(ttl=60.0)
        thread = self._with_probe_in_flight(service)
        try:
            finished = threading.Semaphore(0)
            for _ in range(16):
                threading.Thread(
                    target=lambda: (service.check(), finished.release()),
                    daemon=True,
                ).start()
            for index in range(16):
                self.assertTrue(
                    finished.acquire(timeout=5),
                    f'contender {index} was still blocked by the hung probe',
                )
            self.assertEqual(self.probe_calls, 1)
        finally:
            self._drain(service, thread)

    def test_warm_cache_contender_is_served_the_previous_verdict(self):
        """Stale-while-revalidate: a seconds-old verdict beats a stalled request."""
        healthy = ReadinessService(
            {DEPENDENCY_CENTRAL_DB: lambda: None}, ttl=0.0,
        )
        warm = healthy.check()
        self.assertTrue(warm.ready)

        service = self._service(ttl=0.0)
        # Seed the cache with the healthy verdict, then hang the next refresh.
        service._snapshot = warm  # noqa: SLF001 — seeding the state under test
        thread = self._with_probe_in_flight(service)
        try:
            snapshot = service.check()
            self.assertTrue(
                snapshot.ready,
                'the last known verdict must be served while a refresh is in flight',
            )
        finally:
            self._drain(service, thread)

    def test_provisional_verdict_is_not_cached(self):
        """The contended answer must never outlive the contention that caused it."""
        service = self._service(ttl=60.0)
        thread = self._with_probe_in_flight(service)
        try:
            self.assertFalse(service.check().ready)
        finally:
            self._drain(service, thread)
        # The real probe landed and recorded ready; the provisional unavailable
        # must not have been stored as a fresh verdict that shadows it.
        self.assertTrue(service.check().ready)
        self.assertEqual(self.probe_calls, 1, 'the landed verdict must still be cached')

    def test_thread_containment_negative_self_test(self):
        """A blocking single-flight DOES stall contenders — the guard bites.

        Models the pre-fix shape (``with lock:``) over the same hung probe, to
        prove the assertions above fail against it rather than passing
        vacuously.
        """
        lock = threading.Lock()

        def _blocking_check():
            with lock:
                self._hanging_probe()

        thread = threading.Thread(target=_blocking_check, daemon=True)
        thread.start()
        self.assertTrue(self.entered.wait(timeout=5))
        done = threading.Event()
        threading.Thread(
            target=lambda: (_blocking_check(), done.set()), daemon=True,
        ).start()
        self.assertFalse(
            done.wait(timeout=1),
            'the blocking shape must stall the contender (else this test is vacuous)',
        )
        self.release.set()
        thread.join(timeout=10)


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
