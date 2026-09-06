# ⚠️ 2026-09-06: 이 파일은 **헬퍼 18개 · 테스트 0개**로 도착해 있었다. 다시 채운 기록이다.
#
#    2026-08-31 분할이 남긴 것은 파서 헬퍼뿐이고, 네 봉인 섹션이 모두 비어 있었으며
#    `_Token` 정의마저 저쪽에 남았다(F821 8건). 그래서 이 모듈은 **수집되어 0개를
#    돌리고 초록**이었다 — 「봉인이 있다」와 「봉인이 아무것도 안 본다」가 같은 값인 자리.
#
#    ── 왜 지우지 않고 되살렸나 (판정 근거, 전부 실측) ─────────────────────────
#    ① 봉인 대상이 **떠나지 않았다.** `.github/workflows/dependency-audit.yml` 은
#       이 레포에 실재하고(7,840 B) 모노레포 사본과 **바이트 동일**하며, GitHub
#       Actions 에서 `active` 로 PR 마다 실제로 돈다(2026-09-05 실행 10건 전부 success).
#    ② **아무도 대신 지키지 않는다.** 모노레포의 같은 이름 모듈은
#       `REPO_ROOT = parents[1]` 로 **자기 레포의** 워크플로만 본다. 이 레포의
#       `continue-on-error` 드리프트를 보는 눈은 오늘 0개였다.
#    ③ 제외 사유가 **만료됐다.** `tests/RETIRED_WITH_THE_FRONTEND.md`(모노레포) §5 는
#       두 클래스가 못 온 사유를 *"`.github` 워크플로/락파일 등 저쪽 미비 산출물을 요구"*
#       라고 적는다. 오늘 실측: 워크플로 · `apps/web/package-lock.json`(450,584 B) ·
#       `requirements.txt` · `requirements-central.txt` 가 **전부 실재**한다.
#
#    ── 그리고 세 문서가 서로를 가리키고 있었다 (「이사」가 아니라 「소멸」) ────────
#       · 모노레포 사본: *"두 클래스는 2026-08-31 에 fcc-test-platform 으로 **옮겼다**"*
#       · 이 레포 사본: *"두 클래스는 이 레포로 **오지 못했다**"*
#       · RETIRED §5:   *"저쪽 산출물이 미비해 **싣지 않았다**"*
#       셋 다 상대가 갖고 있다고 적었고 **어느 쪽에도 없었다.** 그 문서 스스로
#       *"이 문서가 사라지면 다음 세션이 «원래 없었다»로 읽는다"* 고 경고한 형태다.
#       원형은 도입 커밋(모노레포 `1b6f6f1e`)에서 회수했다 — 재작성이 아니라 복원이다.
#
#    ⚠️ 배치 원칙: 진화한 두 봉인(Shape · IsNonBlocking)은 모노레포의 **최신** 판을,
#       잃어버린 두 봉인(Triggers · EcosystemCoverage)은 도입 커밋 판을 쓴다. 후자는
#       이미 이 파일에 남아 있던 진화한 헬퍼(`_mentions_command` 토큰 경계 ·
#       `_dependency_manifests_on_disk` 디스크 파생)를 그대로 소비하므로 접합이 맞는다.


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
1. **파서**: 아래 stdlib 미니 파서가 1차 경로다 — PyYAML 부재 시 ``skipTest`` 로 도망가면
   초록색으로 보이는 무검증이 되므로 그렇게 하지 않는다. PyYAML 이 있는 환경에서는
   ``test_mini_parser_matches_pyyaml`` 이 두 파서 결과의 구조 동등성을 대조해 미니
   파서가 조용히 틀리는 경우를 잡는다 (검증기의 검증기).

   ⚠️ **2026-09-06 정정.** 이 자리는 *"PyYAML 은 이 repo 의 선언된 의존성이 아니다
   (``requirements*.txt`` / ``pyproject.toml`` 어디에도 없고 전이 설치로만 존재)"* 라고
   적으며 그것을 미니 파서의 사유로 삼고 있었다. **이 레포에서 그 문장은 거짓이다** —
   실측 2026-09-06: ``pyproject.toml`` 의 ``[project.optional-dependencies].test`` 가
   ``PyYAML>=6.0`` 을 선언한다(``requirements.txt`` 에는 없다). 그리고 이 레포에도
   가드 없이 ``import yaml`` 하는 형제 모듈이 둘 있다
   (``test_central_docker_compose.py`` · ``test_auth_mode_pairing.py``) — 그것들은
   선언이 없던 동안 「우연히 깔려 있어」 초록이었다.

   그럼에도 **미니 파서는 남는다.** 사유가 「의존성을 늘리지 않는다」에서 「이 모듈의
   실질 판정이 파서 가용성에 매이지 않는다」로 바뀔 뿐이고, 후자는 앞의 ``skipTest``
   논거가 이미 말하던 것이다. 지우면 PyYAML 없는 러너에서 판정을 잃는다.
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


class _Token:
    __slots__ = ('indent', 'text', 'block')

    def __init__(self, indent: int, text: str, block: str | None) -> None:
        self.indent = indent
        self.text = text
        self.block = block


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
class TestDependencyAuditWorkflowShape(unittest.TestCase):
    def test_workflow_file_exists(self):
        self.assertTrue(
            WORKFLOW_PATH.is_file(),
            f'{WORKFLOW_PATH.relative_to(REPO_ROOT)} 가 사라졌다 — 의존성 감사 게이트가 '
            '조용히 제거되면 upstream CVE 관측이 통째로 없어진다.',
        )

    def test_workflow_parses_as_yaml(self):
        workflow = _workflow()
        self.assertIsInstance(workflow, dict)
        self.assertEqual(workflow.get('name'), 'dependency-audit')
        self.assertIsInstance(workflow.get('jobs'), dict)
        self.assertTrue(workflow['jobs'], 'jobs 가 비어 있다')

    def test_mini_parser_matches_pyyaml(self):
        """검증기의 검증기 — PyYAML 이 있으면 미니 파서 결과와 대조한다.

        ⚠️ 이 대조 **만** 조건부다 (2026-09-05 이후 PyYAML 은 선언돼 있지만, 이
        모듈의 실질 판정이 파서 가용성에 매여서는 안 된다). 실질 판정
        (비차단/생태계/paths)은 미니 파서로 무조건 수행된다.
        """
        try:
            import yaml  # type: ignore
        except Exception:  # pragma: no cover - PyYAML 미설치 환경
            self.skipTest('PyYAML 미설치 — 미니 파서 parity 대조만 생략 (실질 판정은 무조건 수행)')
        reference = _normalize_bool_keys(yaml.safe_load(WORKFLOW_PATH.read_text(encoding='utf-8')))
        self.assertEqual(
            _workflow(),
            reference,
            'stdlib 미니 파서 결과가 PyYAML 과 갈렸다 — 파서가 워크플로를 잘못 읽고 '
            '있으므로 아래 불변식 판정을 신뢰할 수 없다.',
        )

    def test_on_key_boolean_trap_is_handled(self):
        """YAML 1.1 함정 명문화: bare ``on`` 은 PyYAML 에서 boolean 키가 된다."""
        try:
            import yaml  # type: ignore
        except Exception:  # pragma: no cover
            self.skipTest('PyYAML 미설치')
        raw = yaml.safe_load(WORKFLOW_PATH.read_text(encoding='utf-8'))
        self.assertIn(
            True, raw,
            'bare ``on:`` 이 boolean 키로 파싱되지 않았다 — 함정 전제가 바뀌었으니 '
            '_triggers() 를 재검토하라.',
        )
        self.assertTrue(_triggers(_workflow()), '_triggers() 가 트리거 섹션을 놓쳤다 (vacuous 위험)')


# --------------------------------------------------------------------------- #
# 2. 트리거 + paths 해석 (AC-2)
# --------------------------------------------------------------------------- #
class TestDependencyAuditTriggers(unittest.TestCase):
    def test_triggers_cover_schedule_dispatch_and_pull_request(self):
        triggers = _triggers(_workflow())
        for expected in ('schedule', 'workflow_dispatch', 'pull_request'):
            self.assertIn(expected, triggers, f'{expected} 트리거 누락')

    def test_schedule_declares_a_cron(self):
        schedule = _triggers(_workflow())['schedule']
        self.assertIsInstance(schedule, list)
        self.assertTrue(
            any(isinstance(entry, dict) and entry.get('cron') for entry in schedule),
            'cron 미선언 — 새 advisory 는 코드 변경 없이 나타나므로 주기 실행이 필요하다.',
        )

    def test_pull_request_paths_resolve_to_existing_files(self):
        """non-vacuous: 글롭 문자열 존재가 아니라 실제 해석 결과를 단언한다."""
        paths = _pull_request_paths()
        self.assertTrue(paths, 'paths 필터가 비어 있다')
        for pattern in paths:
            matched = list(REPO_ROOT.glob(pattern))
            self.assertTrue(
                matched,
                f'paths 패턴 {pattern!r} 이 이 저장소의 어떤 파일과도 매칭되지 않는다 '
                '— 트리거가 썩었다(manifest 이름 변경/삭제).',
            )

    def test_pull_request_paths_contain_only_dependency_manifests(self):
        """paths 필터는 **의존성 manifest 전용**이다.

        워크플로 파일 자신이나 소스 경로가 섞이면 "manifest 가 바뀌면 감사한다" 는
        트리거 의미가 흐려지고, manifest 와 무관한 변경에도 감사가 도는 잡음이 된다.
        allowlist 는 하드코딩이 아니라 디스크에서 파생한다(``_dependency_manifests_on_disk``).
        """
        allowed = _dependency_manifests_on_disk()
        self.assertTrue(allowed, '의존성 manifest 를 하나도 찾지 못했다 (판정이 vacuous)')
        for pattern in _pull_request_paths():
            matched = {
                match.relative_to(REPO_ROOT).as_posix() for match in REPO_ROOT.glob(pattern)
            }
            self.assertTrue(matched, f'paths 패턴 {pattern!r} 이 아무 파일과도 매칭되지 않는다')
            self.assertEqual(
                matched - allowed, set(),
                f'paths 패턴 {pattern!r} 이 의존성 manifest 가 아닌 경로를 포함한다 '
                f'(allowed={sorted(allowed)}).',
            )

    def test_pull_request_paths_cover_both_ecosystems(self):
        resolved = _resolved_trigger_paths()
        python_manifests = {name for name in resolved if re.fullmatch(r'requirements.*\.txt', name)}
        self.assertTrue(python_manifests, f'Python manifest 미커버 (resolved={sorted(resolved)})')

        lockfiles = {
            name for name in LOCKFILE_AUDIT_COMMANDS if (WEB_DIR / name).is_file()
        }
        self.assertTrue(lockfiles, 'apps/web 에 알려진 lockfile 이 없다 — 생태계 판별 불가')
        for lockfile in lockfiles:
            self.assertIn(
                f'apps/web/{lockfile}', resolved,
                f'frontend lockfile apps/web/{lockfile} 이 paths 필터에서 빠졌다.',
            )

    def test_all_python_requirement_manifests_are_covered(self):
        """새 ``requirements-*.txt`` 가 생겨도 트리거가 자동 커버해야 한다."""
        resolved = _resolved_trigger_paths()
        on_disk = {p.name for p in REPO_ROOT.glob('requirements*.txt')}
        self.assertTrue(on_disk, 'requirements*.txt 가 하나도 없다')
        self.assertEqual(
            on_disk - resolved, set(),
            'paths 필터가 커버하지 않는 Python manifest 가 있다 — 글롭을 유지하라.',
        )


# --------------------------------------------------------------------------- #
# 3. 생태계 커버리지 (AC-1 전반부)
# --------------------------------------------------------------------------- #
class TestDependencyAuditEcosystemCoverage(unittest.TestCase):
    def test_python_pip_audit_step_present(self):
        runs = [str(step.get('run') or '') for _, step in _steps(_workflow())]
        self.assertTrue(
            any(_mentions_command(run, PYTHON_AUDIT_COMMAND) for run in runs),
            'pip-audit 를 실행하는 step 이 없다 — Python 생태계 미커버.',
        )

    def test_python_audit_targets_requirements_manifests(self):
        runs = [str(step.get('run') or '') for _, step in _steps(_workflow())]
        audit_runs = [run for run in runs if _mentions_command(run, PYTHON_AUDIT_COMMAND)]
        self.assertTrue(
            any('requirements' in run for run in audit_runs),
            'pip-audit step 이 requirements manifest 를 대상으로 삼지 않는다 '
            '(설치만 하고 감사하지 않는 vacuous 구성).',
        )

    def test_frontend_audit_command_matches_actual_lockfile(self):
        """lockfile 실재 여부가 SSOT — npm/pnpm/yarn 을 추측하지 않는다."""
        present = [name for name in LOCKFILE_AUDIT_COMMANDS if (WEB_DIR / name).is_file()]
        self.assertEqual(
            len(present), 1,
            f'apps/web 의 lockfile 이 정확히 1개가 아니다: {present}',
        )
        expected_command = LOCKFILE_AUDIT_COMMANDS[present[0]]
        stale_commands = set(LOCKFILE_AUDIT_COMMANDS.values()) - {expected_command}

        runs = [str(step.get('run') or '') for _, step in _steps(_workflow())]
        self.assertTrue(
            any(_mentions_command(run, expected_command) for run in runs),
            f'lockfile 이 {present[0]} 인데 {expected_command!r} step 이 없다.',
        )
        for stale in stale_commands:
            self.assertFalse(
                any(_mentions_command(run, stale) for run in runs),
                f'lockfile 생태계와 불일치하는 {stale!r} 이 남아 있다.',
            )

    def test_frontend_audit_runs_inside_web_workspace(self):
        workflow = _workflow()
        expected_command = LOCKFILE_AUDIT_COMMANDS[
            next(name for name in LOCKFILE_AUDIT_COMMANDS if (WEB_DIR / name).is_file())
        ]
        for job_id, job in workflow['jobs'].items():
            job_default = ((job.get('defaults') or {}).get('run') or {}).get('working-directory')
            for step in (job.get('steps') or []):
                if not _mentions_command(str(step.get('run') or ''), expected_command):
                    continue
                working_dir = step.get('working-directory') or job_default
                self.assertEqual(
                    working_dir, 'apps/web',
                    f'{_step_label(job_id, step)} 이 apps/web 밖에서 실행된다 — '
                    'lockfile 을 못 찾아 감사가 무의미해진다.',
                )
                return
        self.fail(f'{expected_command!r} step 을 찾지 못했다')


# --------------------------------------------------------------------------- #
# 4. 비차단 봉인 (AC-1 핵심)
# --------------------------------------------------------------------------- #
class TestDependencyAuditIsNonBlocking(unittest.TestCase):
    """이 게이트는 **의도적으로** 정보성이다 — 차단성으로의 우발적 격상을 막는다."""

    def test_every_audit_step_is_non_blocking(self):
        workflow = _workflow()
        audit_steps = _audit_steps(workflow)
        self.assertTrue(audit_steps, '감사 step 을 하나도 찾지 못했다 (판정이 vacuous)')
        for job_id, step in audit_steps:
            self.assertIs(
                step.get('continue-on-error'), True,
                f'{_step_label(job_id, step)} 에 continue-on-error: true 가 없다 — '
                '의존성 감사가 차단성 게이트가 되면 무관한 upstream advisory 하나로 '
                '모든 PR 이 red 가 된다 (워크플로 헤더의 설계 결정 참조).',
            )

    def test_audit_jobs_are_non_blocking(self):
        workflow = _workflow()
        audit_job_ids = {job_id for job_id, _ in _audit_steps(workflow)}
        self.assertTrue(audit_job_ids)
        for job_id in sorted(audit_job_ids):
            self.assertIs(
                workflow['jobs'][job_id].get('continue-on-error'), True,
                f'job {job_id!r} 에 continue-on-error: true 가 없다 — 설치/셋업 step '
                '실패만으로도 workflow 결론이 오염된다.',
            )

    def test_both_ecosystems_are_sealed_non_blocking(self):
        """**이 저장소에 있는** 생태계마다 비차단 감사 step 이 실재해야 한다.

        ⚠️ 2026-08-31 — 옛 형태는 «둘»을 상수로 세었다. 프론트엔드가
        `fcc-test-platform` 으로 이사하자 그 상수가 **없는 생태계를 요구**해
        영구 red 가 됐다. 생태계 목록은 선언이 아니라 **이 트리에 무엇이 있는가**로
        파생한다 — 그래야 다음 이사에도 따라간다.

        ⚠️ 그리고 파생이 **비지 않아야** 한다. 목록이 0개면 이 검사는 아무것도
        요구하지 않으면서 초록이 된다 — 「감사가 있다」와 「생태계가 없다」가
        같은 값이 되는 자리다.
        """
        audit_runs = [str(step.get('run') or '') for _, step in _audit_steps(_workflow())]
        expected: list[tuple[str, str]] = []
        if any(REPO_ROOT.glob("requirements*.txt")):
            expected.append(('Python', PYTHON_AUDIT_COMMAND))
        lockfile = next(
            (name for name in LOCKFILE_AUDIT_COMMANDS if (WEB_DIR / name).is_file()), None
        )
        if lockfile is not None:
            expected.append(('frontend', LOCKFILE_AUDIT_COMMANDS[lockfile]))
        self.assertTrue(
            expected,
            '감사 대상 생태계를 하나도 파생하지 못했다 — 파생이 깨졌다면 이 검사는 '
            '아무것도 요구하지 않으면서 초록이 된다',
        )
        for label, token in expected:
            self.assertTrue(
                any(_mentions_command(run, token) for run in audit_runs),
                f'{label} 생태계의 비차단 감사 step 이 없다.',
            )

    def test_findings_are_emitted_to_step_summary(self):
        """차단하지 않는 대신 결과가 보여야 한다 — 안 보이면 게이트의 가치가 0."""
        audit_runs = [str(step.get('run') or '') for _, step in _audit_steps(_workflow())]
        self.assertTrue(
            any(STEP_SUMMARY_VAR in run for run in audit_runs),
            f'감사 결과가 ${STEP_SUMMARY_VAR} 로 나가지 않는다 — 비차단 게이트의 '
            '유일한 가치는 가시성이다.',
        )


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
