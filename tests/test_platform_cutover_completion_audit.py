import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fcc_test_platform.cutover_bundle_cli import EVIDENCE_FILENAMES
from fcc_test_platform.cutover_completion_audit_cli import build_completion_audit
from fcc_test_platform.cutover_completion_audit_cli import _bundle as _audit_bundle

from fcc_test_contracts.common.tree_artifacts import resolve_dependency_artifact


from tests._moved_module_source import moved_module_source

project_root = Path(__file__).parent.parent


def _local_function_names(module_path: Path) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding='utf-8'))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _primitives_module_name() -> str:
    """placeholder 원시연산 모듈의 **dotted name** — 모듈에게 묻는다.

    ``__name__`` 은 개명을 따라오고, 이 파일의 리터럴은 따라오지 않는다.
    """
    from fcc_test_platform import evidence_primitives

    return evidence_primitives.__name__


def _imports_from(module_path: Path, target_module: str) -> bool:
    tree = ast.parse(module_path.read_text(encoding='utf-8'))
    return any(
        isinstance(node, ast.ImportFrom) and (node.module or '').endswith(target_module)
        for node in ast.walk(tree)
    )


class TestPlaceholderDetectionSsotDedup(unittest.TestCase):
    """Commit 2 — angle-bracket placeholder detection is single-sourced in
    src/application/headless/platform_evidence_primitives.py; the two cutover
    scripts must delegate, not redefine it."""

    #: ⚠️ **이름을 여기 리터럴로 적지 않는다** (2026-09-03).
    #: 여기 있던 값은 `'platform_evidence_primitives'` 였고, 추출이 그 모듈을
    #: `fcc_test_platform.evidence_primitives` 로 개명하면서 **낡았다** — 두
    #: 스크립트는 계속 위임하고 있었는데 이 검사만 red 였다(선언된 부채).
    #:
    #: 새 이름을 갈아 끼우면 다음 개명에 또 낡는다. 그래서 **모듈 객체에게 묻는다** —
    #: 같은 파일의 형제 검사가 이미 그것을 import 하므로 SSOT 가 하나다.
    PRIMITIVES = _primitives_module_name()
    #: ⚠️ 2026-08-31 — hints 는 `scripts/` 에서 **패키지로** 옮겼다(휠이 나르지
    #: 못하는 자리였고, 그래서 모노레포의 컷오버 판정 도구가 죽어 있었다).
    #: 위임 명제는 그대로다 — 어디에 살든 원시연산을 재정의하면 안 된다.
    #: ⚠️ 2026-09-05 — **같은 일이 세 번째로 일어났다.** 이번엔
    #: `platform_cutover_live_workflow` 의 알맹이가
    #: `fcc_test_platform.cutover_live_workflow_cli` 로 갔고, `scripts/` 쪽에는 22줄
    #: 진입점만 남았다. 그 껍데기는 원시연산을 위임하지 «않으므로» 이 검사가 red 가
    #: 됐다 — 위임 명제는 여전히 참인데 **읽는 대상이 알맹이가 아니었다.**
    #:
    #: 그래서 이제 **둘 다 경로가 아니라 모듈에게 묻는다**. 다음에 또 옮겨도
    #: 이 검사는 따라간다.
    SCRIPTS = (
        moved_module_source('fcc_test_platform.cutover_workflow_hints'),
        moved_module_source('fcc_test_platform.cutover_live_workflow_cli'),
    )

    def test_scripts_have_no_local_placeholder_definitions(self):
        for script in self.SCRIPTS:
            local = _local_function_names(script)
            self.assertNotIn('is_placeholder_token', local, script.name)
            self.assertNotIn('_is_placeholder_token', local, script.name)
            self.assertNotIn('_placeholder_tokens', local, script.name)

    def test_scripts_import_placeholder_primitives(self):
        for script in self.SCRIPTS:
            self.assertTrue(
                _imports_from(script, self.PRIMITIVES),
                f'{script.name} must import from {self.PRIMITIVES}',
            )

    def test_primitive_scanner_matches_workflow_hint_token_extraction(self):
        from fcc_test_platform.evidence_primitives import placeholder_tokens
        from fcc_test_platform.cutover_workflow_hints import (
            placeholder_tokens as hint_tokens,
            suggested_command,
        )
        for evidence_key in EVIDENCE_FILENAMES:
            command = suggested_command(evidence_key, 'out.json')
            expected = sorted({t for part in command for t in placeholder_tokens(str(part))})
            self.assertEqual(hint_tokens(command), expected, evidence_key)


class TestPlatformCutoverCompletionAudit(unittest.TestCase):
    def test_context_read_errors_are_opaque_for_hostile_context_paths(self):
        hostile_values = (
            'postgresql://user:dsn-password@private.example/live',
            'https://alice:url-password@private.example/callback',
            '/var/lib/fcc/token-like/bearer-token-value.json',
            'ARBITRARY_CONTEXT_EXCEPTION_SENTINEL',
        )
        for context_name in ('central_db_schema', 'extraction_manifest'):
            argument_name = f'{context_name}_path'
            for hostile_value in hostile_values:
                with self.subTest(context_name=context_name, hostile_value=hostile_value):
                    with tempfile.TemporaryDirectory() as tmpdir:
                        kwargs = {argument_name: Path(hostile_value)}
                        audit = build_completion_audit(
                            evidence_root=Path(tmpdir) / 'evidence',
                            provider_id='fcc-unlicensed-conducted',
                            cutover_candidate_id='cutover-test',
                            evaluated_at='2026-05-15T12:30:00+09:00',
                            collected_at='2026-05-15T12:30:00+09:00',
                            **kwargs,
                        )

                    issue = next(
                        issue for issue in audit['issues']
                        if issue['code'] == 'context_read_error'
                    )
                    self.assertEqual(issue['path'], context_name)
                    self.assertEqual(issue['category'], 'validation_context')
                    self.assertEqual(issue['message'], 'validation context could not be read')
                    serialized = json.dumps(audit, sort_keys=True)
                    self.assertNotIn(hostile_value, serialized)
                    self.assertNotIn('dsn-password', serialized)
                    self.assertNotIn('url-password', serialized)
                    self.assertNotIn('bearer-token-value', serialized)
                    self.assertNotIn('ARBITRARY_CONTEXT_EXCEPTION_SENTINEL', serialized)

    def test_missing_evidence_root_blocks_completion_with_prompt_checklist(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = build_completion_audit(
                evidence_root=Path(tmpdir) / 'missing-root',
                provider_id='fcc-unlicensed-conducted',
                cutover_candidate_id='cutover-test',
                evaluated_at='2026-05-15T12:30:00+09:00',
                collected_at='2026-05-15T12:30:00+09:00',
                central_db_schema_path=project_root / 'docs' / 'platform' / 'central_db_schema.v1.json',
                extraction_manifest_path=resolve_dependency_artifact('docs/api/headless_contract_extraction_manifest.v1.json'),
            )

        self.assertFalse(audit['complete'])
        self.assertEqual(audit['decision'], 'not_complete')
        self.assertEqual(audit['issue_count'], len(EVIDENCE_FILENAMES))
        self.assertEqual(audit['diagnostics']['issue_count'], len(EVIDENCE_FILENAMES))
        self.assertEqual(audit['diagnostics']['by_code']['missing_evidence_file'], len(EVIDENCE_FILENAMES))
        self.assertIn('ingestion_execution', audit['diagnostics']['missing_evidence_keys'])
        checklist_ids = {item['id'] for item in audit['checklist']}
        self.assertIn('live_postgresql_ingestion_execution', checklist_ids)
        self.assertIn('db_only_report_reconstruction', checklist_ids)
        self.assertIn('deployment_idp_frontend_browser_qa', checklist_ids)
        self.assertIn('final_cutover_bundle_gate', checklist_ids)
        self.assertIn('ingestion_execution', {
            issue['evidence_key']
            for issue in audit['issues']
            if issue.get('code') == 'missing_evidence_file'
        })
        self.assertEqual(len(audit['next_commands']), len(EVIDENCE_FILENAMES))
        ingestion_command = next(item for item in audit['next_commands'] if item['evidence_key'] == 'ingestion_execution')
        self.assertEqual(ingestion_command['step_id'], 'ingestion-execution')
        self.assertEqual(
            ingestion_command['suggested_command'][:3],
            ['python', 'scripts/platform_ingestion_execution_evidence.py', 'execute'],
        )
        self.assertIn('postgres-dsn', ingestion_command['placeholder_tokens'])
        self.assertIn('ingestion-evidence-id', ingestion_command['placeholder_tokens'])
        report_command = next(item for item in audit['next_commands'] if item['evidence_key'] == 'db_only_report_reconstruction')
        self.assertIn('report-run-id', report_command['placeholder_tokens'])
        self.assertIn('reviewer', report_command['placeholder_tokens'])
        self.assertNotIn('missing_manifest', {issue['code'] for issue in audit['issues']})
        self.assertNotIn('context_read_error', {issue['code'] for issue in audit['issues']})

    def test_placeholder_manifests_are_not_proxy_completion(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for filename in EVIDENCE_FILENAMES.values():
                (root / filename).write_text('{}', encoding='utf-8')

            audit = build_completion_audit(
                evidence_root=root,
                provider_id='fcc-unlicensed-conducted',
                cutover_candidate_id='cutover-test',
                evaluated_at='2026-05-15T12:30:00+09:00',
                collected_at='2026-05-15T12:30:00+09:00',
                central_db_schema_path=project_root / 'docs' / 'platform' / 'central_db_schema.v1.json',
                extraction_manifest_path=resolve_dependency_artifact('docs/api/headless_contract_extraction_manifest.v1.json'),
            )

        self.assertFalse(audit['complete'])
        self.assertIn('missing_manifest', {issue['code'] for issue in audit['issues']})
        final_gate = next(item for item in audit['checklist'] if item['id'] == 'final_cutover_bundle_gate')
        self.assertEqual(final_gate['status'], 'fail')

    def test_audit_bundle_uses_nested_manifest_identity_when_present(self):
        bundle = _audit_bundle(
            provider_id='fcc-unlicensed-conducted',
            cutover_candidate_id='cutover-test',
            evaluated_at='2026-05-15T12:30:00+09:00',
            collected_at='2026-05-15T12:30:00+09:00',
            manifests={
                key: {
                    'schema_version': 1,
                    'evidence_id': 'nested-evidence-id',
                    'collected_at': '2026-05-15T15:15:00+09:00',
                }
                for key in EVIDENCE_FILENAMES
            },
        )

        service_item = bundle['evidence']['service_deployment']
        self.assertEqual(service_item['evidence_id'], 'nested-evidence-id')
        self.assertEqual(service_item['collected_at'], '2026-05-15T15:15:00+09:00')

    def test_workflow_config_adds_command_hints_to_missing_blockers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workflow_config = root / 'workflow.json'
            workflow_config.write_text(json.dumps({
                'schema_version': 1,
                'steps': [
                    {
                        'id': 'live-ingestion',
                        'evidence_key': 'ingestion_execution',
                        'output': EVIDENCE_FILENAMES['ingestion_execution'],
                        'env': {
                            'FCC_HEADLESS_AUTH_TOKEN': 'real-token-value',
                            'FCC_HEADLESS_PROVIDER_ID': 'fcc-unlicensed-conducted',
                        },
                        'command': ['python', 'scripts/platform_ingestion_execution_evidence.py', 'execute'],
                        'suggested_command': [
                            'python',
                            'scripts/platform_ingestion_execution_evidence.py',
                            'execute',
                            '--output',
                            'artifacts/cutover/evidence/ingestion_execution.json',
                        ],
                    },
                    {
                        'id': 'hardware-smoke',
                        'evidence_key': 'hardware_smoke',
                        'output': EVIDENCE_FILENAMES['hardware_smoke'],
                        'suggested_command': [
                            'python',
                            'scripts/headless_hardware_smoke_evidence.py',
                            'collect',
                        ],
                    },
                ],
            }), encoding='utf-8')

            audit = build_completion_audit(
                evidence_root=root / 'evidence',
                provider_id='fcc-unlicensed-conducted',
                cutover_candidate_id='cutover-test',
                evaluated_at='2026-05-15T12:30:00+09:00',
                collected_at='2026-05-15T12:30:00+09:00',
                central_db_schema_path=project_root / 'docs' / 'platform' / 'central_db_schema.v1.json',
                extraction_manifest_path=resolve_dependency_artifact('docs/api/headless_contract_extraction_manifest.v1.json'),
                workflow_config_path=workflow_config,
            )

        ingestion_hint = audit['workflow_hints']['ingestion_execution']
        self.assertEqual(ingestion_hint['step_id'], 'live-ingestion')
        self.assertEqual(ingestion_hint['env']['FCC_HEADLESS_AUTH_TOKEN'], '<redacted>')
        self.assertEqual(ingestion_hint['env']['FCC_HEADLESS_PROVIDER_ID'], 'fcc-unlicensed-conducted')
        self.assertNotIn('real-token-value', json.dumps(audit))
        self.assertEqual(
            ingestion_hint['suggested_command'][-2:],
            ['--output', 'artifacts/cutover/evidence/ingestion_execution.json'],
        )
        missing_ingestion = next(
            issue
            for issue in audit['issues']
            if issue.get('code') == 'missing_evidence_file'
            and issue.get('evidence_key') == 'ingestion_execution'
        )
        self.assertEqual(missing_ingestion['workflow_hint']['step_id'], 'live-ingestion')
        blocker = next(item for item in audit['missing_blockers'] if item['id'] == 'live_postgresql_ingestion_execution')
        self.assertIn('ingestion_execution', blocker['workflow_hints'])
        next_command = next(item for item in audit['next_commands'] if item['evidence_key'] == 'ingestion_execution')
        self.assertEqual(next_command['step_id'], 'live-ingestion')
        self.assertEqual(next_command['reason_codes'], ['missing_evidence_file'])
        self.assertEqual(
            next_command['suggested_command'][-2:],
            ['--output', 'artifacts/cutover/evidence/ingestion_execution.json'],
        )
        self.assertFalse(next_command['contains_placeholders'])
        self.assertEqual(next_command['placeholder_tokens'], [])

    def test_next_commands_follow_workflow_step_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workflow_config = root / 'workflow.json'
            workflow_config.write_text(json.dumps({
                'schema_version': 1,
                'steps': [
                    {
                        'id': 'hardware-smoke',
                        'evidence_key': 'hardware_smoke',
                        'output': EVIDENCE_FILENAMES['hardware_smoke'],
                        'suggested_command': ['python', 'scripts/headless_hardware_smoke_evidence.py', 'collect'],
                    },
                    {
                        'id': 'live-ingestion',
                        'evidence_key': 'ingestion_execution',
                        'output': EVIDENCE_FILENAMES['ingestion_execution'],
                        'suggested_command': ['python', 'scripts/platform_ingestion_execution_evidence.py', 'execute'],
                    },
                ],
            }), encoding='utf-8')

            audit = build_completion_audit(
                evidence_root=root / 'evidence',
                provider_id='fcc-unlicensed-conducted',
                cutover_candidate_id='cutover-test',
                evaluated_at='2026-05-15T12:30:00+09:00',
                collected_at='2026-05-15T12:30:00+09:00',
                central_db_schema_path=project_root / 'docs' / 'platform' / 'central_db_schema.v1.json',
                extraction_manifest_path=resolve_dependency_artifact('docs/api/headless_contract_extraction_manifest.v1.json'),
                workflow_config_path=workflow_config,
            )

        self.assertEqual(
            [item['evidence_key'] for item in audit['next_commands'][:2]],
            ['hardware_smoke', 'ingestion_execution'],
        )

    def test_workflow_config_duplicate_evidence_key_is_reported_without_overwriting_first_hint(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workflow_config = root / 'workflow.json'
            workflow_config.write_text(json.dumps({
                'schema_version': 1,
                'steps': [
                    {
                        'id': 'first-ingestion',
                        'evidence_key': 'ingestion_execution',
                        'output': EVIDENCE_FILENAMES['ingestion_execution'],
                        'suggested_command': ['python', 'first.py'],
                    },
                    {
                        'id': 'second-ingestion',
                        'evidence_key': 'ingestion_execution',
                        'output': EVIDENCE_FILENAMES['ingestion_execution'],
                        'suggested_command': ['python', 'second.py'],
                    },
                ],
            }), encoding='utf-8')

            audit = build_completion_audit(
                evidence_root=root / 'evidence',
                provider_id='fcc-unlicensed-conducted',
                cutover_candidate_id='cutover-test',
                evaluated_at='2026-05-15T12:30:00+09:00',
                collected_at='2026-05-15T12:30:00+09:00',
                central_db_schema_path=project_root / 'docs' / 'platform' / 'central_db_schema.v1.json',
                extraction_manifest_path=resolve_dependency_artifact('docs/api/headless_contract_extraction_manifest.v1.json'),
                workflow_config_path=workflow_config,
            )

        self.assertIn('workflow_config_duplicate_evidence_key', {issue['code'] for issue in audit['issues']})
        self.assertEqual(audit['workflow_hints']['ingestion_execution']['step_id'], 'first-ingestion')
        ingestion_command = next(item for item in audit['next_commands'] if item['evidence_key'] == 'ingestion_execution')
        self.assertEqual(ingestion_command['suggested_command'], ['python', 'first.py'])

    def test_workflow_config_invalid_steps_and_unknown_keys_are_reported(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workflow_config = root / 'workflow.json'
            workflow_config.write_text(json.dumps({
                'schema_version': 1,
                'steps': [
                    'not-an-object',
                    {
                        'id': 'unknown',
                        'evidence_key': 'unknown_evidence',
                        'output': 'unknown.json',
                    },
                ],
            }), encoding='utf-8')

            audit = build_completion_audit(
                evidence_root=root / 'evidence',
                provider_id='fcc-unlicensed-conducted',
                cutover_candidate_id='cutover-test',
                evaluated_at='2026-05-15T12:30:00+09:00',
                collected_at='2026-05-15T12:30:00+09:00',
                central_db_schema_path=project_root / 'docs' / 'platform' / 'central_db_schema.v1.json',
                extraction_manifest_path=resolve_dependency_artifact('docs/api/headless_contract_extraction_manifest.v1.json'),
                workflow_config_path=workflow_config,
            )

        codes = {issue['code'] for issue in audit['issues']}
        self.assertIn('workflow_config_invalid_step', codes)
        self.assertIn('workflow_config_unknown_evidence_key', codes)
        self.assertIn('workflow_config_invalid_step', audit['diagnostics']['workflow_issue_codes'])
        self.assertIn('workflow_config_unknown_evidence_key', audit['diagnostics']['workflow_issue_codes'])
        self.assertEqual(audit['workflow_hints'], {})

    def test_cli_writes_json_and_returns_one_until_complete(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output = root / 'audit.json'
            commands_output = root / 'next-commands.json'
            workflow_config = root / 'workflow.json'
            workflow_config.write_text(json.dumps({
                'schema_version': 1,
                'steps': [
                    {
                        'id': 'live-ingestion',
                        'evidence_key': 'ingestion_execution',
                        'output': EVIDENCE_FILENAMES['ingestion_execution'],
                        'suggested_command': ['python', 'scripts/platform_ingestion_execution_evidence.py', 'execute'],
                    },
                ],
            }), encoding='utf-8')
            completed = subprocess.run(
                [
                    sys.executable,
                    'scripts/platform_cutover_completion_audit.py',
                    '--evidence-root', str(root / 'evidence'),
                    '--provider-id', 'fcc-unlicensed-conducted',
                    '--cutover-candidate-id', 'cutover-test',
                    '--evaluated-at', '2026-05-15T12:30:00+09:00',
                    '--collected-at', '2026-05-15T12:30:00+09:00',
                    '--central-db-schema', str(project_root / 'docs' / 'platform' / 'central_db_schema.v1.json'),
                    '--extraction-manifest', str(resolve_dependency_artifact('docs/api/headless_contract_extraction_manifest.v1.json')),
                    '--workflow-config', str(workflow_config),
                    '--output', str(output),
                    '--next-commands-output', str(commands_output),
                ],
                cwd=str(project_root),
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
            )

            saved = json.loads(output.read_text(encoding='utf-8'))
            commands = json.loads(commands_output.read_text(encoding='utf-8'))

        self.assertEqual(completed.returncode, 1)
        self.assertFalse(saved['complete'])
        self.assertIn('ingestion_execution', saved['workflow_hints'])
        self.assertIn('ingestion_execution', {item['evidence_key'] for item in saved['next_commands']})
        self.assertIn('ingestion_execution', {item['evidence_key'] for item in commands})
        self.assertEqual(json.loads(completed.stdout)['decision'], 'not_complete')


class TestAuditBundleHonorsSelfReportedValidation(unittest.TestCase):
    """Commit 3 — _bundle must not synthesize validated=True/issues=[] over a
    manifest that self-reports invalid evidence."""

    def _manifests(self, overrides: dict | None = None) -> dict:
        manifests = {
            key: {'evidence_id': f'{key}-id', 'collected_at': '2026-05-15T12:30:00+09:00'}
            for key in EVIDENCE_FILENAMES
        }
        if overrides:
            manifests.update(overrides)
        return manifests

    def test_manifest_self_reported_invalid_is_propagated(self):
        manifests = self._manifests({
            'performance_smoke': {
                'evidence_id': 'perf-id',
                'collected_at': '2026-05-15T12:30:00+09:00',
                'validated': False,
                'issues': ['p95 exceeded'],
            },
        })
        bundle = _audit_bundle(
            provider_id='fcc-unlicensed-conducted',
            cutover_candidate_id='cutover-test',
            evaluated_at='2026-05-15T12:30:00+09:00',
            collected_at='2026-05-15T12:30:00+09:00',
            manifests=manifests,
        )

        self.assertFalse(bundle['evidence']['performance_smoke']['validated'])
        self.assertEqual(bundle['evidence']['performance_smoke']['issues'], ['p95 exceeded'])
        # Manifests without the fields keep the legacy default (validated/empty).
        self.assertTrue(bundle['evidence']['service_deployment']['validated'])
        self.assertEqual(bundle['evidence']['service_deployment']['issues'], [])

    def test_self_reported_invalid_blocks_completion(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for key, filename in EVIDENCE_FILENAMES.items():
                manifest = {'evidence_id': f'{key}-id', 'collected_at': '2026-05-15T12:30:00+09:00'}
                if key == 'performance_smoke':
                    manifest['validated'] = False
                (root / filename).write_text(json.dumps(manifest), encoding='utf-8')

            audit = build_completion_audit(
                evidence_root=root,
                provider_id='fcc-unlicensed-conducted',
                cutover_candidate_id='cutover-test',
                evaluated_at='2026-05-15T12:30:00+09:00',
                collected_at='2026-05-15T12:30:00+09:00',
                central_db_schema_path=project_root / 'docs' / 'platform' / 'central_db_schema.v1.json',
                extraction_manifest_path=resolve_dependency_artifact('docs/api/headless_contract_extraction_manifest.v1.json'),
            )

        self.assertFalse(audit['complete'])
        self.assertIn('evidence_not_validated', {issue['code'] for issue in audit['issues']})

    def test_audit_rejects_placeholder_filled_manifests(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for key, filename in EVIDENCE_FILENAMES.items():
                # Operator copies the suggested-command template tokens into a
                # manifest instead of collecting real evidence.
                (root / filename).write_text(json.dumps({
                    'evidence_id': f'<{key}-evidence-id>',
                    'collected_at': '<ISO-8601 timestamp>',
                    'provider_id': '<provider-id>',
                }), encoding='utf-8')

            audit = build_completion_audit(
                evidence_root=root,
                provider_id='fcc-unlicensed-conducted',
                cutover_candidate_id='cutover-test',
                evaluated_at='2026-05-15T12:30:00+09:00',
                collected_at='2026-05-15T12:30:00+09:00',
                central_db_schema_path=project_root / 'docs' / 'platform' / 'central_db_schema.v1.json',
                extraction_manifest_path=resolve_dependency_artifact('docs/api/headless_contract_extraction_manifest.v1.json'),
            )

        self.assertFalse(audit['complete'])
        self.assertIn('placeholder_evidence_value', {issue['code'] for issue in audit['issues']})
        final_gate = next(item for item in audit['checklist'] if item['id'] == 'final_cutover_bundle_gate')
        self.assertEqual(final_gate['status'], 'fail')


class TestCutoverEvidenceKeySsot(unittest.TestCase):
    """Commit 3 — the 14 evidence keys are single-sourced; the four declared
    inventories must stay set-equal."""

    def test_evidence_key_inventories_are_set_equal(self):
        from fcc_test_platform.cutover_readiness import CUTOVER_REQUIRED_EVIDENCE

        schema = json.loads(
            (project_root / 'docs' / 'platform' / 'cutover_readiness_evidence.schema.v1.json')
            .read_text(encoding='utf-8')
        )
        reference = set(EVIDENCE_FILENAMES)
        self.assertEqual(set(CUTOVER_REQUIRED_EVIDENCE), reference, 'CUTOVER_REQUIRED_EVIDENCE')
        self.assertEqual(set(schema['required_evidence_keys']), reference, 'schema.required_evidence_keys')
        self.assertEqual(
            set(schema['manifest_required_evidence_keys']),
            reference,
            'schema.manifest_required_evidence_keys',
        )
        self.assertEqual(len(reference), 14)


if __name__ == '__main__':
    unittest.main()
