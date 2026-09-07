#!/usr/bin/env python3
"""L1 — 기여자(비개발자) 세션이 «코드 파일»을 편집하는 것을 막는다.

■ 무엇을 위한 것인가

동료들은 비개발자이고 전 작업을 Claude Code 로 한다. 그분들이 해야 하는 일은
`intent/<slug>/intent.md` 와 `spec.md` 뿐이다. 코드는 개발 담당자의 몫이다.
이 훅은 **실수하려고 해도 못 하게** 해서, 마음 놓고 쓰게 만든다.

■ ⚠️ 기본값은 «무해»하다 — 그것이 설계다

`.claude/role` 파일이 없거나 그 안에 `contributor` 가 없으면 **아무것도 막지
않는다.** 이 저장소는 여러 세션이 동시에 만지고, 이 훅이 기본으로 차단하면
형제 세션들이 통보 없이 멈춘다. 켜는 것은 그 PC 의 «선택»이다:

    echo contributor > .claude/role      # 이 체크아웃을 기여자 모드로

■ ⚠️ 이것은 실수 방지층이지 방어층이 아니다

`.claude/settings.json` 을 지우거나 `.claude/role` 을 고치면 사라진다.
진짜 강제는 L4(branch protection)뿐이다 — `.claude/rules/intent-workflow.md`.
이 문단이 지워지면 다음 사람이 이것을 강제라고 믿는다.

■ 공식 문서 근거 (code.claude.com/docs/en/hooks)

    · stdin 으로 `{tool_name, tool_input, cwd, ...}` JSON 을 받는다
    · 차단은 **exit 2**, 이유는 stdout 의
      `hookSpecificOutput.permissionDecision = "deny"` 와
      `permissionDecisionReason`
    · ⚠️ 워크트리에서는 `${CLAUDE_PROJECT_DIR}` 가 세션 시작 위치에 «머물고»
      `cwd` 필드가 현재 워크트리를 가리킨다 → **`cwd` 를 쓴다**

■ 실패 자세

읽을 수 없는 입력·없는 파일·예외는 전부 **fail-open**(exit 0)이다.
위생 게이트가 오탐을 내면 사람들이 그것을 꺼 버린다.
"""
from __future__ import annotations

import json
from pathlib import PurePosixPath
import sys

#: 기여자가 «건드리면 안 되는» 최상위 경로.
#: 좁게 센다 — 넓히면 문서 작업까지 막고, 그러면 훅이 꺼진다.
CODE_ROOTS = ('fcc_test_platform', 'scripts', 'tests', 'migrations',
              'infra', 'web', 'apps', 'packages', 'config', 'githooks')

#: 기여자가 «만져도 되는» 것. 코드 루트보다 먼저 본다.
ALLOWED_PREFIXES = ('intent/',)

ROLE_FILE = '.claude/role'
CONTRIBUTOR = 'contributor'


def _deny(reason: str) -> int:
    print(json.dumps({
        'hookSpecificOutput': {
            'hookEventName': 'PreToolUse',
            'permissionDecision': 'deny',
            'permissionDecisionReason': reason,
        }
    }, ensure_ascii=False))
    return 2


def decide(payload: dict) -> tuple[int, str]:
    """(종료코드, 사유). 0 이면 아무 결정도 하지 않는다(정상 권한 흐름)."""
    from pathlib import Path

    cwd = payload.get('cwd') or '.'
    role_path = Path(cwd) / ROLE_FILE
    try:
        role = role_path.read_text(encoding='utf-8').strip().lower()
    except OSError:
        return 0, ''                      # 역할 선언이 없다 → 무해
    if role != CONTRIBUTOR:
        return 0, ''

    raw = (payload.get('tool_input') or {}).get('file_path')
    if not isinstance(raw, str) or not raw:
        return 0, ''                      # 경로를 못 봤다 → fail-open

    try:
        rel = str(Path(raw).resolve().relative_to(Path(cwd).resolve()))
    except (ValueError, OSError):
        rel = raw                         # 저장소 밖이거나 해소 실패 → 원문으로 본다
    rel = rel.replace('\\', '/')

    if any(rel.startswith(p) for p in ALLOWED_PREFIXES):
        return 0, ''
    parts = PurePosixPath(rel).parts
    if not parts or parts[0] not in CODE_ROOTS:
        return 0, ''

    return 2, (
        f'이 세션은 «기여자» 역할입니다({ROLE_FILE}). 코드 파일을 편집할 수 없습니다: {rel}\n'
        '\n'
        '기여자가 하는 일은 둘입니다:\n'
        '  · intent/<슬러그>/intent.md  — 왜 이걸 하는가\n'
        '  · intent/<슬러그>/spec.md    — 무엇을 어떤 설계로 (의도 승인 뒤)\n'
        '\n'
        '코드는 개발 담당자의 몫입니다. 규칙: intent/README.md\n'
        '개발 세션이라면 이 파일을 지우거나 내용을 바꾸세요: ' + ROLE_FILE
    )


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or '{}')
    except (json.JSONDecodeError, OSError):
        return 0                          # 입력을 못 읽었다 → fail-open
    if not isinstance(payload, dict):
        return 0
    try:
        code, reason = decide(payload)
    except Exception as exc:                       # noqa: BLE001 — fail-open 이 요점
        print(f'contributor-role guard: 판정 실패 — {exc} (통과시킨다)', file=sys.stderr)
        return 0
    return _deny(reason) if code == 2 else 0


if __name__ == '__main__':
    raise SystemExit(main())
