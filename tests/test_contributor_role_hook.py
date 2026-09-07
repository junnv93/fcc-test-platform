"""L1 훅(`.claude/hooks/guard_contributor_role.py`)의 봉인.

■ 세 축이다

    1. 켜지 않았을 때 «아무것도 막지 않는가»  ← 가장 중요하다
    2. 켰을 때 코드를 «막는가»
    3. 켰을 때 intent 를 «막지 않는가»

1번이 가장 중요한 이유: 이 저장소는 여러 세션이 동시에 만진다. 기본값이
차단이면 형제 세션들이 통보 없이 멈추고, 그러면 사람들이 훅을 지운다.

■ 공식 문서 근거 (code.claude.com/docs/en/hooks)

    · 차단은 exit 2 + stdout 의 `hookSpecificOutput.permissionDecision = "deny"`
    · 워크트리에서 `${CLAUDE_PROJECT_DIR}` 는 세션 시작 위치에 «머물고»
      `cwd` 필드가 현재 워크트리를 가리킨다 → 훅은 `cwd` 를 봐야 한다
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / '.claude' / 'hooks' / 'guard_contributor_role.py'


def _run(cwd: Path, file_path: str, tool: str = 'Write') -> tuple[int, str]:
    payload = json.dumps({
        'hook_event_name': 'PreToolUse', 'tool_name': tool, 'cwd': str(cwd),
        'tool_input': {'file_path': file_path},
    })
    proc = subprocess.run([sys.executable, str(HOOK)], input=payload,
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout


def _sandbox(td: str, *, role: str | None) -> Path:
    root = Path(td)
    (root / '.claude').mkdir(parents=True, exist_ok=True)
    (root / 'scripts').mkdir(exist_ok=True)
    (root / 'intent' / 'a').mkdir(parents=True, exist_ok=True)
    if role is not None:
        (root / '.claude' / 'role').write_text(role + '\n', encoding='utf-8')
    return root


class TestItIsInertUntilTurnedOn(unittest.TestCase):
    """기본값은 무해해야 한다 — 형제 세션을 통보 없이 멈추면 안 된다."""

    def test_no_role_file_blocks_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = _sandbox(td, role=None)
            rc, _ = _run(root, str(root / 'scripts' / 'x.py'))
        self.assertEqual(0, rc, '역할 선언이 없는데 막았다 — 형제 세션이 멈춘다')

    def test_a_developer_role_blocks_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = _sandbox(td, role='developer')
            rc, _ = _run(root, str(root / 'scripts' / 'x.py'))
        self.assertEqual(0, rc)


class TestItBlocksCodeForAContributor(unittest.TestCase):

    def test_a_code_file_is_denied_with_the_documented_shape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = _sandbox(td, role='contributor')
            rc, out = _run(root, str(root / 'scripts' / 'x.py'))
        self.assertEqual(2, rc, '공식 문서상 차단은 exit 2 다')
        payload = json.loads(out)
        self.assertEqual('deny',
                         payload['hookSpecificOutput']['permissionDecision'])
        self.assertEqual('PreToolUse',
                         payload['hookSpecificOutput']['hookEventName'])
        reason = payload['hookSpecificOutput']['permissionDecisionReason']
        self.assertIn('intent/README.md', reason,
                      '막으면서 «다음에 무엇을 해야 하는지»를 말하지 않았다')
        self.assertIn('.claude/role', reason,
                      '개발 세션이 이것을 끄는 법을 말하지 않았다 — '
                      '끄는 법을 모르면 훅 자체를 지운다')

    def test_the_intent_folder_is_not_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = _sandbox(td, role='contributor')
            rc, _ = _run(root, str(root / 'intent' / 'a' / 'intent.md'))
        self.assertEqual(0, rc, '기여자가 «해야 하는» 일을 막았다')

    def test_a_document_outside_the_code_roots_is_not_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = _sandbox(td, role='contributor')
            (root / 'docs').mkdir(exist_ok=True)
            rc, _ = _run(root, str(root / 'docs' / 'x.md'))
        self.assertEqual(0, rc, '좁게 세야 한다 — 넓히면 훅이 꺼진다')


class TestItFailsOpen(unittest.TestCase):
    """위생 게이트가 오탐을 내면 사람들이 꺼 버린다."""

    def test_unreadable_input_passes(self) -> None:
        proc = subprocess.run([sys.executable, str(HOOK)], input='not json',
                              capture_output=True, text=True)
        self.assertEqual(0, proc.returncode)

    def test_a_missing_file_path_passes(self) -> None:
        proc = subprocess.run([sys.executable, str(HOOK)],
                              input='{"tool_name":"Write","cwd":"/tmp","tool_input":{}}',
                              capture_output=True, text=True)
        self.assertEqual(0, proc.returncode)


class TestTheWiringExists(unittest.TestCase):
    """훅을 만든 것과 그것이 «불리는» 것은 다른 명제다."""

    def test_settings_json_registers_it_for_edit_and_write(self) -> None:
        cfg = json.loads((ROOT / '.claude' / 'settings.json').read_text(encoding='utf-8'))
        entries = cfg['hooks']['PreToolUse']
        matchers = [e['matcher'] for e in entries]
        self.assertTrue(any('Edit' in m and 'Write' in m for m in matchers),
                        f'Edit/Write 를 잡는 matcher 가 없다: {matchers}')
        commands = [h['command'] for e in entries for h in e['hooks']]
        self.assertTrue(any('guard_contributor_role.py' in c for c in commands),
                        f'훅이 등록되지 않았다: {commands}')

    def test_it_uses_cwd_not_the_project_dir(self) -> None:
        """워크트리에서 `${CLAUDE_PROJECT_DIR}` 는 세션 시작 위치에 «머문다».

        공식 문서: "cwd follows Claude … the cwd field in the hook's input JSON
        is the worktree root after Claude enters a worktree".
        `CLAUDE_PROJECT_DIR` 로 역할 파일을 찾으면 워크트리에서 틀린 곳을 본다.
        """
        src = HOOK.read_text(encoding='utf-8')
        self.assertIn("payload.get('cwd')", src)
        self.assertNotIn('CLAUDE_PROJECT_DIR', src.split('"""', 2)[-1],
                         '훅 «본문»이 CLAUDE_PROJECT_DIR 를 쓴다 — 워크트리에서 틀린다')

    def test_it_declares_that_it_is_not_a_defence_layer(self) -> None:
        src = HOOK.read_text(encoding='utf-8')
        self.assertIn('실수 방지층이지 방어층이 아니다', src,
                      '이 문단이 지워지면 다음 사람이 이것을 강제라고 믿는다')


if __name__ == '__main__':
    unittest.main()
