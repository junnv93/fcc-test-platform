"""시료 API 응답이 **자기 선언과 같은 모양인가** (2026-09-06).

## Why — 실제로 시험원이 만난 것

접수 화면에서 시료를 만들면 **매번**(3/3 재현) 「The request failed.」가 떴다.
생성은 성공했는데도. 네트워크에 이것이 있었다:

    200 POST  /platform/projects/{id}/samples
    503 GET   /platform/projects/{id}/samples/undefined      ← 문자열 "undefined"

원인은 **구현이 자기 선언을 어긴 것**이다:

    선언   POST …/samples  200 → SampleInventoryItem  (required: sample_id, …)
    실제   {"…", "id": "2fbcc542-…", "project_id": "…"}   ← sample_id 가 없다

프런트엔드는 **선언을 믿는 것이 옳다** — 타입 클라이언트가 그 선언에서 생성되므로
`updated.sample_id` 는 타입상 `string` 이다. 값이 `undefined` 라 URL 에
`?sample=undefined` 가 들어갔고, 그 다음 조회가 503 을 냈다.

⚠️ **타입 검사가 이것을 잡을 수 없다.** 프런트는 선언과 일치하고 서버도 파이썬
타입상 문제가 없다. 갈라진 것은 «선언과 런타임 값»이고, 그 축을 보는 검사가 이
저장소에 없었다. 읽기 경로는 SQL 에서 `s."id" AS "sample_id"` 로 별칭을 붙여 맞았고,
쓰기 경로만 손으로 dict 를 지으며 `id` 를 썼다 — **같은 자원이 읽기와 쓰기에서 다른
이름을 답하고 있었다.**

## What — 왜 「전부 통과」가 아니라 「선언과 일치」인가

이 검사를 처음 붙였을 때 **오늘 이미 있던 갈라짐 둘이 더 나왔다.** 그것까지 이
웨이브에서 고치면 범위가 조용히 넘치고, 무시하면 검사가 거짓 초록이 된다.
그래서 `scripts/lane_check.py` 와 같은 형태를 쓴다:

    관측된 갈라짐 집합 == 선언된 갈라짐 집합(`KNOWN_DRIFT`)

그러면 셋이 한꺼번에 성립한다.

* 오늘 이미 있던 갈라짐은 **통과**로 읽힌다 (팀원을 헛되이 막지 않는다).
* 새 갈라짐은 **즉시 red** 다.
* 고쳐진 갈라짐도 **red** 다 — 선언이 낡았다는 뜻이고, 그것도 소식이다.

⚠️ `KNOWN_DRIFT` 를 **늘려서 초록을 만들지 마라.** 그것은 검사를 끄는 것이다.

## 대상 집합은 선언에서 파생한다

OpenAPI 에서 시료 경로들의 200 응답 `$ref` 를 읽어 연산 집합을 만든다. 새
엔드포인트가 선언되면 `test_every_declared_operation_is_exercised` 가 빨개진다 —
그러지 않으면 새 엔드포인트는 「선언과 갈라져도 아무도 안 보는」 상태로 태어난다.
실제로 이 축이 **이 파일을 쓴 사람의 누락 셋을 먼저 잡았다.**

## 한계

`jsonschema` 의 오류 «문구»로 갈라짐을 식별한다. 라이브러리가 문구를 바꾸면 이
검사는 «새 갈라짐»으로 읽고 빨개진다 — 조용히 통과하지는 않으므로 안전한 방향의
취약함이다. 그리고 여기서 도는 것은 SQLite 심이므로 PostgreSQL 전용 표현이 낳는
차이는 이 축이 답하지 못한다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import unittest

import jsonschema

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact
from fcc_test_platform.application.central_sample_inventory_read_adapter import (
    PostgresCentralSampleInventoryReadAdapter,
)
from fcc_test_platform.application.central_sample_inventory_service import (
    CentralSampleInventoryService,
)
from fcc_test_platform.application.central_sample_inventory_write_adapter import (
    PostgresCentralSampleInventoryWriteAdapter,
)
from tests.support.central_pg_sqlite_shim import QmarkConnection
from tests.support.sample_inventory_central import make_central_db, seed_project

_ARTIFACT = resolve_repo_artifact(__file__, 'docs/api/platform-api.openapi.json')
PROJECT_ID = 'project-declaration'

SAMPLES = '/platform/projects/{project_id}/samples'
ONE = SAMPLES + '/{sample_id}'
CUSTODY = ONE + '/custody-events'

#: 2026-09-06 에 **실측된** 갈라짐. 이 웨이브가 고친 것은 `sample_id` 축이고,
#: 아래 둘은 그 전부터 있던 별개의 갈라짐이다.
#:
#: ⚠️ 이 표를 늘려서 초록을 만들지 마라. 고치거나, 고치지 않는 이유를 적어라.
#:
#: * `intake_count` — 선언은 required 인데 쓰기 응답 넷이 담지 않는다. 읽기 경로는
#:   LEFT JOIN 으로 세어 담는다. 담으려면 쓰기마다 세는 질의가 하나 더 붙으므로
#:   **비용 판단이 필요하고** 이 웨이브의 질문이 아니다.
#: * `model_id` — 읽기 응답이 선언에 없는 칸을 더 준다. 지울지 선언에 넣을지는
#:   그 칸의 소유(프로젝트 축 vs 시료 축) 판정이 앞서야 한다. OpenAPI 는
#:   **생성물**이라 손으로 고칠 수 없다.
KNOWN_DRIFT: dict[tuple[str, str], str] = {
    ('post', SAMPLES): "'intake_count' is a required property",
    ('patch', ONE): "'intake_count' is a required property",
    ('post', ONE + '/status'): "'intake_count' is a required property",
    ('delete', ONE): "'intake_count' is a required property",
    ('get', ONE): "Additional properties are not allowed ('model_id' was unexpected)",
}


def _document() -> dict:
    return json.loads(Path(_ARTIFACT).read_text(encoding='utf-8'))


def _ref(doc: dict, method: str, path: str) -> str | None:
    op = (doc.get('paths', {}).get(path) or {}).get(method) or {}
    schema = ((op.get('responses', {}).get('200', {})
               .get('content', {}).get('application/json', {})) or {}).get('schema', {})
    return schema.get('$ref')


def _drift(doc: dict, method: str, path: str, instance: object) -> str | None:
    """선언과 어긋나면 그 «문구», 맞으면 ``None``."""
    ref = _ref(doc, method, path)
    assert ref is not None, f'{method.upper()} {path} 의 200 응답 $ref 가 없다'
    try:
        jsonschema.validate(instance, {'$ref': ref, 'components': doc['components']})
    except jsonschema.ValidationError as exc:
        return exc.message
    return None


def _declared_operations(doc: dict) -> set[tuple[str, str]]:
    """시료 경로들 중 200 응답을 «선언한» 연산 전부 — 손으로 나열하지 않는다."""
    return {
        (method, path)
        for path, node in doc.get('paths', {}).items()
        if path == SAMPLES or path.startswith(ONE)
        for method in ('get', 'post', 'patch', 'delete')
        if _ref(doc, method, path)
    }


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = _document()
        self.db_path = make_central_db()
        seed_project(self.db_path, PROJECT_ID, model_name='SM-DECL-01')
        self.service = CentralSampleInventoryService(
            PostgresCentralSampleInventoryReadAdapter(lambda: QmarkConnection(self.db_path)),
            PostgresCentralSampleInventoryWriteAdapter(lambda: QmarkConnection(self.db_path)),
        )

    def tearDown(self) -> None:
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    def _create(self, number: str = 'D-1') -> dict:
        return self.service.create_sample(
            PROJECT_ID, {'sample_number': number, 'sample_code': number},
            actor_subject='user:pm',
        )

    def _observe(self) -> dict[tuple[str, str], str]:
        """모든 시료 연산을 **실제로 돌려** 갈라짐만 모은다."""
        svc, doc = self.service, self.doc
        seen: dict[tuple[str, str], str] = {}

        def note(method: str, path: str, instance: object) -> None:
            message = _drift(doc, method, path, instance)
            if message is not None:
                seen[(method, path)] = message

        created = self._create()
        note('post', SAMPLES, created)
        sid = created['sample_id']

        event = svc.append_custody_event(
            PROJECT_ID, sid,
            {'event_type': 'received', 'occurred_on': '2026-09-06'},
            actor_subject='user:pm',
        )
        note('post', CUSTODY, event)
        note('get', CUSTODY, svc.list_custody_events(PROJECT_ID, sid))
        note('delete', CUSTODY + '/{event_id}', svc.delete_custody_event(
            PROJECT_ID, sid, event['custody_event_id'], actor_subject='user:pm'))

        note('get', ONE, svc.get_sample(PROJECT_ID, sid))
        note('get', ONE + '/history', svc.list_history(PROJECT_ID, sid))
        note('get', ONE + '/intakes', svc.list_intakes(PROJECT_ID, sid))

        patched = svc.patch_sample(
            PROJECT_ID, sid, {'note': 'n'},
            expected_version=created['row_version'], actor_subject='user:pm')
        note('patch', ONE, patched)
        note('post', ONE + '/status', svc.change_status(
            PROJECT_ID, sid, 'deleted',
            expected_version=patched['row_version'], actor_subject='user:pm'))

        other = self._create('D-2')
        note('delete', ONE, svc.soft_delete(
            PROJECT_ID, other['sample_id'],
            expected_version=other['row_version'], actor_subject='user:pm'))
        return seen


class TestTheDeclarationIsReadable(_Fixture):
    """읽지 못한 것을 초록으로 세지 않는다."""

    def test_the_artifact_exists(self) -> None:
        self.assertTrue(Path(_ARTIFACT).is_file(), f'{_ARTIFACT} 가 없다')

    def test_every_declared_operation_is_exercised(self) -> None:
        exercised = {
            ('post', SAMPLES), ('get', ONE), ('patch', ONE), ('delete', ONE),
            ('post', ONE + '/status'), ('get', ONE + '/history'),
            ('get', ONE + '/intakes'),
            ('post', CUSTODY), ('get', CUSTODY), ('delete', CUSTODY + '/{event_id}'),
        }
        missing = _declared_operations(self.doc) - exercised
        self.assertEqual(
            missing, set(),
            f'선언에 있는데 이 검사가 돌리지 않는 시료 연산: {sorted(missing)}. '
            '엔드포인트가 늘면 이 파일도 따라와야 한다 — 그러지 않으면 새 엔드포인트는 '
            '「선언과 갈라져도 아무도 안 보는」 상태로 태어난다.',
        )

    def test_this_file_does_not_name_an_undeclared_operation(self) -> None:
        for method, path in sorted(KNOWN_DRIFT):
            self.assertIsNotNone(
                _ref(self.doc, method, path),
                f'{method.upper()} {path} 는 선언에 없다 — KNOWN_DRIFT 가 낡았다',
            )


class TestObservedDriftMatchesTheDeclaredSet(_Fixture):
    """⚠️ 이 파일의 본체."""

    def test_the_drift_set_is_exactly_what_is_declared(self) -> None:
        observed = self._observe()
        unexpected = {k: v for k, v in observed.items() if k not in KNOWN_DRIFT}
        healed = sorted(set(KNOWN_DRIFT) - set(observed))
        changed = {
            k: (KNOWN_DRIFT[k], observed[k])
            for k in set(observed) & set(KNOWN_DRIFT)
            if observed[k] != KNOWN_DRIFT[k]
        }
        self.assertEqual(
            unexpected, {},
            f'선언에 없는 새 갈라짐: {unexpected}. 방금 깨뜨린 것이다 — '
            'KNOWN_DRIFT 를 늘리지 말고 응답을 선언에 맞춰라.',
        )
        self.assertEqual(
            healed, [],
            f'KNOWN_DRIFT 에 적혔는데 이제 맞는 연산: {healed}. 고쳐진 것이다 — '
            '그 줄을 지워라(선언이 낡으면 다음 갈라짐이 가려진다).',
        )
        self.assertEqual(changed, {}, f'같은 연산인데 갈라짐 «내용»이 바뀌었다: {changed}')

    def test_the_identifier_axis_is_clean_everywhere(self) -> None:
        """이 웨이브가 고친 축은 «따로» 못박는다 — KNOWN_DRIFT 뒤에 숨지 않게."""
        observed = self._observe()
        for (method, path), message in observed.items():
            self.assertNotIn(
                "'id'", message,
                f'{method.upper()} {path} 가 아직 자원 키를 `id` 로 답한다: {message}',
            )
            self.assertNotIn('sample_id', message, f'{method.upper()} {path}: {message}')
            self.assertNotIn('custody_event_id', message, f'{method.upper()} {path}: {message}')


class TestTheCheckWouldSeeTheDefect(_Fixture):
    """봉인이 오늘 초록인 것과 이빨이 있는 것은 다른 명제다.

    2026-09-06 «이전»의 실제 응답 모양을 재현해 검사에 먹인다.
    """

    def test_the_old_create_shape_is_rejected(self) -> None:
        old = dict(self._create())
        old['id'] = old.pop('sample_id')
        message = _drift(self.doc, 'post', SAMPLES, old)
        self.assertIsNotNone(message, '옛 모양이 통과한다 — 이 봉인은 이빨이 없다')
        # ⚠️ 어느 «문구»로 거부되는지는 jsonschema 가 정한다. 옛 모양은 두 규칙을
        #    한꺼번에 어긴다(선언에 없는 `id` · 빠진 `sample_id`) — 둘 중 무엇이
        #    먼저 보고되든 거부는 거부다. 문구 하나에 못박으면 라이브러리 판이
        #    바뀌는 날 이 «이빨 확인»이 거짓 red 가 된다.
        self.assertIn('id', str(message))

    def test_the_old_custody_shape_is_rejected(self) -> None:
        sample = self._create()
        event = dict(self.service.append_custody_event(
            PROJECT_ID, sample['sample_id'],
            {'event_type': 'received', 'occurred_on': '2026-09-06'},
            actor_subject='user:pm',
        ))
        event['id'] = event.pop('custody_event_id')
        message = _drift(self.doc, 'post', CUSTODY, event)
        self.assertIsNotNone(message, '옛 custody 모양이 통과한다 — 이빨이 없다')
        self.assertIn('id', str(message))


if __name__ == '__main__':
    unittest.main()
