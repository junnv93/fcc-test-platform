import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fcc_test_platform.frontend_qa_evidence import FRONTEND_REQUIRED_VIEWS
from tests._moved_module_source import moved_module_source


project_root = Path(__file__).parent.parent
# ⚠️ 여기 있던 `CLI_PATH`(= `scripts/…` 껍데기 경로)은 2026-09-05 에 «죽은 상수»가
#    됐다. 그것을 쓰던 유일한 자리가 경계 단언이었고, 그 단언이 읽어야 할 것은
#    껍데기가 아니라 알맹이였다(아래 `_GUTS_SOURCE`). 껍데기를 하위 프로세스로
#    부르는 자리는 상수가 아니라 리터럴 경로를 쓴다 — 되살릴 이유가 없다.
# ⚠️ **경계는 «알맹이»에게 물어야 한다** — 껍데기는 22줄이라 어떤 금지 import 도
# 없고, 그것을 읽는 단언은 «참이지만 아무것도 재지 않는 참»이 된다. 경로가 아니라
# 모듈에게 묻는다 (`tests/_moved_module_source.py`).
_GUTS_SOURCE = moved_module_source('fcc_test_platform.frontend_qa_evidence_cli')



def _valid_manifest() -> dict:
    return {
        'schema_version': 1,
        'evidence_id': 'frontend-qa-1',
        'collected_at': '2026-05-15T15:00:00+09:00',
        'app_url': 'https://platform.example.com',
        'browser': 'Chromium 125',
        'auth_flow_verified': True,
        'central_backend_verified': True,
        'provider_contract_routes_verified': True,
        'viewport_results': [
            {
                'name': 'desktop',
                'width': 1440,
                'height': 900,
                'rendered': True,
                'responsive_pass': True,
                'views_verified': list(FRONTEND_REQUIRED_VIEWS),
                'console_errors': [],
                'failed_requests': [],
            }
        ],
        'screenshots': [
            {
                'viewport': 'desktop',
                'relative_path': 'frontend/desktop-overview.png',
                'sha256': 'a' * 64,
            }
        ],
    }


class TestPlatformFrontendQaEvidenceCli(unittest.TestCase):
    def test_valid_manifest_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'frontend.json'
            path.write_text(json.dumps(_valid_manifest()), encoding='utf-8')

            completed = _run_cli('validate', str(path))

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertEqual(json.loads(completed.stdout), {'valid': True, 'issues': []})

    def test_invalid_manifest_returns_one(self):
        manifest = _valid_manifest()
        manifest['central_backend_verified'] = False
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'frontend.json'
            path.write_text(json.dumps(manifest), encoding='utf-8')

            completed = _run_cli('validate', str(path))

        self.assertEqual(completed.returncode, 1)
        self.assertIn('central_backend_not_verified', {issue['code'] for issue in json.loads(completed.stdout)['issues']})

    def test_read_error_returns_two(self):
        completed = _run_cli('validate', 'missing.json')

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(completed.stdout)['issues'][0]['code'], 'read_error')

    def test_template_does_not_claim_browser_qa(self):
        completed = _run_cli('template', '--evidence-id', 'frontend-template', '--app-url', 'https://platform.example.com')

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['evidence_id'], 'frontend-template')
        self.assertFalse(payload['auth_flow_verified'])
        self.assertFalse(payload['central_backend_verified'])
        self.assertIn('replace with an empty list after QA', payload['viewport_results'][0]['console_errors'])

    def test_cli_import_boundary_excludes_browser_runtime_dependencies(self):
        tree = ast.parse(_GUTS_SOURCE.read_text(encoding='utf-8'))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        forbidden = {'requests', 'httpx', 'fastapi', 'subprocess', 'sqlite3', 'sqlalchemy', 'playwright', 'selenium', 'os'}
        self.assertTrue(
            imports,
            f'{_GUTS_SOURCE} 가 아무것도 import 하지 않는다 — 알맹이가 또 옮겨갔다면 '
            '이 검사도 «새 자리»를 가리키게 고쳐라. 이 팔을 지우면 아래 경계 단언이 '
            '«참이지만 아무것도 재지 않는 참»으로 되돌아간다.',
        )

        self.assertFalse(sorted(forbidden.intersection(imports)))


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, 'scripts/platform_frontend_qa_evidence.py', *args],
        cwd=str(project_root),
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


if __name__ == '__main__':
    unittest.main()
