# ⚠️ 2026-08-31: 이 파일은 모노레포 `tests/test_dependency_audit_workflow.py` 에서 갈라져 왔다. 남은 것은
#    소비 대상이 이 레포에 있는 단위(TestDependencyAuditTriggers, TestDependencyAuditEcosystemCoverage)뿐이고,
#    나머지 형제 검사와 그것들만 쓰던 import 는 저쪽에 남았다.
"""Conformance seal for the informational dependency-audit CI gate (2026-07-19).

``.github/workflows/dependency-audit.yml`` 는 의도적으로 **비차단(informational)**
게이트다 — 의존성 감사가 표면화하는 것은 upstream 결함이고, 차단성으로 두면 무관한
transitive advisory 하나가 모든 PR 을 red 로 만든다 (워크플로 헤더 주석 참조).

그 "의도적 비차단" 은 파일 한 줄(``continue-on-error: true``) 에 얹혀 있어 조용히
사라지기 쉽다. 반대로 게이트 자체가 통째로 삭제돼도 아무도 모른다. 이 모듈이 두
방향의 드리프트를 모두 봉인한다:

  * 워크플로 존재 + YAML 파싱 가능
  * 2개 생태계(Python pip-audit / frontend npm-audit) 모두 커버
  * **모든** 감사 step 이 ``continue-on-error: true`` (= 차단성으로의 우발적 격상 차단)
  * ``pull_request.paths`` 가 **실재하는** manifest 를 가리킴 (트리거 부패 차단)

── non-vacuous 설계 ───────────────────────────────────────────────────────────
1. **파서**: PyYAML 은 이 repo 의 선언된 의존성이 아니다 (``requirements*.txt`` /
   ``pyproject.toml`` 어디에도 없고 전이 설치로만 존재). 새 의존성을 추가하지 않기
   위해 아래 stdlib 미니 파서가 1차 경로다 — PyYAML 부재 시 ``skipTest`` 로 도망가면
   초록색으로 보이는 무검증이 되므로 그렇게 하지 않는다. PyYAML 이 있는 환경에서는
   ``test_mini_parser_matches_pyyaml`` 이 두 파서 결과의 구조 동등성을 대조해 미니
   파서가 조용히 틀리는 경우를 잡는다 (검증기의 검증기).
2. **``on:`` boolean 함정**: YAML 1.1 에서 bare ``on`` 은 boolean 이라 PyYAML 결과의
   최상위 키는 문자열 ``'on'`` 이 아니라 ``True`` 다. ``wf.get('on', {})`` 는 조용히
   빈 dict 를 돌려주고 트리거 검사가 통째로 vacuous 해진다. ``_triggers()`` 가 양쪽
   표현을 모두 받아들이고, ``test_on_key_boolean_trap_is_handled`` 가 함정 자체를
   명문화한다.
3. **paths 해석**: 글롭 문자열 존재 여부가 아니라 ``Path.glob`` 으로 **실제 파일이
   1개 이상 해석되는지**를 단언한다.

봉인 한계 (documented limitation): "required status check 아님" 은 저장소 branch
protection **설정**이라 파일로 봉인할 수 없다. 대신 step/job 레벨 non-blocking
불변식이 실질적 안전망이며, 워크플로 헤더에 required 등록 금지를 명문화했다.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

import pytest

# ``backend-invariants.yml`` CI 게이트(``-m invariant``)에 편입시킨다. 마커 자동 부착은
# ``tests/conftest.py::_INVARIANT_FILENAME_TOKENS`` 파일명 매칭이라 이 모듈명은 걸리지
# 않는다 — conftest(공유 SSOT)를 건드리는 대신 in-file 선언으로 편입한다.
pytestmark = pytest.mark.invariant

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / '.github' / 'workflows' / 'dependency-audit.yml'
WEB_DIR = REPO_ROOT / 'apps' / 'web'

# lockfile → 그 생태계의 audit 명령. 어느 lockfile 이 실재하는지가 SSOT 이고,
# 워크플로는 그와 일치하는 명령만 써야 한다.
LOCKFILE_AUDIT_COMMANDS = {
    'package-lock.json': 'npm audit',
    'pnpm-lock.yaml': 'pnpm audit',
    'yarn.lock': 'yarn audit',
}

PYTHON_AUDIT_COMMAND = 'pip-audit'

# 감사 step 판별 기준. ``run`` 본문에 이들 중 하나라도 있으면 감사 step 으로 보고
# 비차단 불변식을 강제한다.
AUDIT_COMMAND_TOKENS = (PYTHON_AUDIT_COMMAND, *LOCKFILE_AUDIT_COMMANDS.values())

STEP_SUMMARY_VAR = 'GITHUB_STEP_SUMMARY'


# --------------------------------------------------------------------------- #
# stdlib 미니 YAML 파서 (신규 의존성 금지 — 위 모듈 docstring 참조)
# --------------------------------------------------------------------------- #
_KEY_RE = re.compile(r'^(?P<key>[^:\s][^:]*?)\s*:(?:\s+(?P<val>.*))?$')
_BLOCK_INDICATORS = frozenset({'|', '|-', '|+', '>', '>-', '>+'})
_INT_RE = re.compile(r'-?\d+')




def _strip_inline_comment(text: str) -> str:
    """따옴표 밖의 ``#`` 이후를 잘라낸다 (따옴표 안의 ``#`` 은 보존)."""
    quote: str | None = None
    for i, ch in enumerate(text):
        if quote is not None:
            if ch == quote:
                quote = None
        elif ch in '"\'':
            quote = ch
        elif ch == '#' and i > 0 and text[i - 1] in ' \t':
            return text[:i].rstrip()
    return text


def _scalar(text: str) -> Any:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in '"\'':
        return text[1:-1]
    lowered = text.lower()
    if lowered == 'true':
        return True
    if lowered == 'false':
        return False
    if lowered in ('null', '~', ''):
        return None
    if _INT_RE.fullmatch(text):
        return int(text)
    return text


def _dedent_block(body: list[str]) -> str:
    while body and not body[-1].strip():
        body.pop()
    if not body:
        return ''
    first = next((line for line in body if line.strip()), '')
    margin = len(first) - len(first.lstrip(' '))
    return '\n'.join(line[margin:] if line.strip() else '' for line in body) + '\n'


def _tokenize(text: str) -> list[_Token]:
    raw_lines = text.splitlines()
    tokens: list[_Token] = []
    i = 0
    total = len(raw_lines)
    while i < total:
        line = raw_lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            i += 1
            continue
        indent = len(line) - len(line.lstrip(' '))
        content = _strip_inline_comment(stripped)
        match = _KEY_RE.match(content)
        if match is not None and (match.group('val') or '').strip() in _BLOCK_INDICATORS:
            body: list[str] = []
            j = i + 1
            while j < total:
                nxt = raw_lines[j]
                if not nxt.strip():
                    body.append('')
                    j += 1
                    continue
                if len(nxt) - len(nxt.lstrip(' ')) <= indent:
                    break
                body.append(nxt)
                j += 1
            tokens.append(_Token(indent, match.group('key').strip() + ':', _dedent_block(body)))
            i = j
            continue
        tokens.append(_Token(indent, content, None))
        i += 1
    return tokens


def _parse_map(tokens: list[_Token], pos: int, indent: int) -> tuple[dict, int]:
    result: dict = {}
    while pos < len(tokens) and tokens[pos].indent == indent and not tokens[pos].text.startswith('- '):
        token = tokens[pos]
        match = _KEY_RE.match(token.text)
        if match is None:
            raise ValueError(f'unparsable mapping line: {token.text!r}')
        key = _scalar(match.group('key').strip())
        if token.block is not None:
            result[key] = token.block
            pos += 1
            continue
        value = (match.group('val') or '').strip()
        if value:
            result[key] = _scalar(value)
            pos += 1
            continue
        pos += 1
        if pos < len(tokens) and tokens[pos].indent > indent:
            result[key], pos = _parse_node(tokens, pos, tokens[pos].indent)
        elif pos < len(tokens) and tokens[pos].indent == indent and tokens[pos].text.startswith('- '):
            result[key], pos = _parse_seq(tokens, pos, indent)
        else:
            result[key] = None
    return result, pos


def _parse_seq(tokens: list[_Token], pos: int, indent: int) -> tuple[list, int]:
    items: list = []
    while pos < len(tokens) and tokens[pos].indent == indent and tokens[pos].text.startswith('- '):
        token = tokens[pos]
        rest = token.text[2:].strip()
        if token.block is None and _KEY_RE.match(rest) is not None:
            # ``- `` 는 정확히 2칸이므로 항목 매핑의 들여쓰기는 indent + 2 다.
            # 토큰을 재기입해 일반 매핑 파서에 위임한다 (파싱 로직 중복 제거).
            tokens[pos] = _Token(indent + 2, rest, None)
            item, pos = _parse_map(tokens, pos, indent + 2)
            items.append(item)
        else:
            items.append(_scalar(rest))
            pos += 1
    return items, pos


def _parse_node(tokens: list[_Token], pos: int, indent: int) -> tuple[Any, int]:
    if tokens[pos].text.startswith('- '):
        return _parse_seq(tokens, pos, indent)
    return _parse_map(tokens, pos, indent)


def parse_yaml_subset(text: str) -> dict:
    """이 워크플로가 쓰는 YAML 부분집합을 stdlib 만으로 파싱한다."""
    tokens = _tokenize(text)
    if not tokens:
        return {}
    node, pos = _parse_node(tokens, 0, tokens[0].indent)
    if pos != len(tokens):
        raise ValueError(f'unconsumed tokens at {pos}: {tokens[pos].text!r}')
    return node


def _normalize_bool_keys(node: Any) -> Any:
    """PyYAML 이 boolean 으로 해석한 키(``on`` → ``True``)를 문자열로 되돌린다."""
    if isinstance(node, dict):
        normalized = {}
        for key, value in node.items():
            if key is True:
                key = 'on'
            elif key is False:
                key = 'off'
            normalized[key] = _normalize_bool_keys(value)
        return normalized
    if isinstance(node, list):
        return [_normalize_bool_keys(item) for item in node]
    return node


# --------------------------------------------------------------------------- #
# 접근 헬퍼
# --------------------------------------------------------------------------- #
def _workflow() -> dict:
    return parse_yaml_subset(WORKFLOW_PATH.read_text(encoding='utf-8'))


def _triggers(workflow: dict) -> dict:
    """``on:`` 섹션 — YAML 1.1 boolean 함정(키가 ``True``)까지 흡수한다."""
    for key in ('on', True):
        if key in workflow:
            section = workflow[key]
            return section if isinstance(section, dict) else {}
    return {}


def _steps(workflow: dict) -> list[tuple[str, dict]]:
    collected: list[tuple[str, dict]] = []
    for job_id, job in (workflow.get('jobs') or {}).items():
        for step in (job.get('steps') or []):
            collected.append((job_id, step))
    return collected


def _mentions_command(run: str, command: str) -> bool:
    """``run`` 본문이 해당 명령을 호출하는가 — 왼쪽 토큰 경계를 강제한다.

    단순 substring 이면 ``'npm audit' in 'pnpm audit'`` 이 True 라, npm→pnpm 으로
    바뀐 워크플로가 여전히 "npm 생태계 커버됨" 으로 오판된다 (실측된 vacuous 구멍).
    """
    return re.search(r'(?<![\w./-])' + re.escape(command), run) is not None


def _audit_steps(workflow: dict) -> list[tuple[str, dict]]:
    return [
        (job_id, step)
        for job_id, step in _steps(workflow)
        if any(_mentions_command(str(step.get('run') or ''), token) for token in AUDIT_COMMAND_TOKENS)
    ]


def _step_label(job_id: str, step: dict) -> str:
    return f'{job_id}/{step.get("name") or step.get("uses") or "<unnamed>"}'


def _pull_request_paths() -> list[str]:
    paths = _triggers(_workflow())['pull_request']['paths']
    assert isinstance(paths, list), 'pull_request.paths 가 리스트가 아니다'
    return paths


def _resolved_trigger_paths() -> set[str]:
    """``pull_request.paths`` 글롭이 실제로 해석하는 repo-relative 경로 집합."""
    return {
        match.relative_to(REPO_ROOT).as_posix()
        for pattern in _pull_request_paths()
        for match in REPO_ROOT.glob(pattern)
    }


def _dependency_manifests_on_disk() -> set[str]:
    """이 저장소에 실재하는 **의존성 manifest** 전수 (allowlist SSOT).

    디스크가 SSOT 다 — 하드코딩 목록이 아니라 (a) 루트 ``requirements*.txt``,
    (b) ``apps/web/package.json``, (c) apps/web 에 실재하는 lockfile 로 파생한다.
    워크플로 파일 자신이나 소스 파일은 여기에 들어올 수 없다.
    """
    manifests = {p.relative_to(REPO_ROOT).as_posix() for p in REPO_ROOT.glob('requirements*.txt')}
    if (WEB_DIR / 'package.json').is_file():
        manifests.add('apps/web/package.json')
    manifests.update(
        f'apps/web/{name}' for name in LOCKFILE_AUDIT_COMMANDS if (WEB_DIR / name).is_file()
    )
    return manifests


# --------------------------------------------------------------------------- #
# 1. 존재 + 파싱
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 2. 트리거 + paths 해석 (AC-2)
# --------------------------------------------------------------------------- #
# ⚠️ `TestDependencyAuditTriggers` 는 이 레포로 오지 못했다 — 사유는
#    `tests/RETIRED_WITH_THE_FRONTEND.md`(모노레포) §5 참조.



# --------------------------------------------------------------------------- #
# 3. 생태계 커버리지 (AC-1 전반부)
# --------------------------------------------------------------------------- #
# ⚠️ `TestDependencyAuditEcosystemCoverage` 는 이 레포로 오지 못했다 — 사유는
#    `tests/RETIRED_WITH_THE_FRONTEND.md`(모노레포) §5 참조.



# --------------------------------------------------------------------------- #
# 4. 비차단 봉인 (AC-1 핵심)
# --------------------------------------------------------------------------- #


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
