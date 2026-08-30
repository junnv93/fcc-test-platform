"""AST 가드 robustness helper SSOT (자기 audit #2 정공, 2026-05-29).

본 turn 3 sprint (wlan_fcc_table_duty / AntennaIdentifier / ExcelAntennaAlias)
의 AST 가드가 단순 `ast.Constant` 검사라 다음 우회 가능:
  - f-string: `f"ALL{1}+ALL{2}"` (JoinedStr 안의 FormattedValue + Constant 합성)
  - string concat: `'ALL' + '1'` (BinOp Add 양쪽 정적 합성)
  - dict/list/tuple literal value 내 nested literal (재귀 검사 부재)
  - case variant (대소문자 차이로 우회)

본 helper 가 3 sprint AST 가드 들이 위임할 공통 SSOT. 진정한 SSOT 격상이
되도록 우회 차단.
"""
from __future__ import annotations

import ast
from typing import AbstractSet, Iterable


def find_string_literals_anywhere(
    tree: ast.AST,
    target_set: AbstractSet[str],
    *,
    case_insensitive: bool = False,
) -> list[tuple[int, str]]:
    """AST 노드 트리에서 target_set 의 string literal 사용처를 robust 검출.

    검출 대상:
      1. `ast.Constant` (단순 string literal)
      2. dict/list/tuple literal value 내 nested `ast.Constant` (재귀)
      3. f-string `ast.JoinedStr` 의 정적 합성 가능한 결과
      4. `ast.BinOp(op=ast.Add())` 양쪽 모두 정적 string 인 경우 (concat)

    Args:
        tree: 검사 대상 AST 노드 (모듈 / 함수 / 클래스 등).
        target_set: 검출할 string literal 집합.
        case_insensitive: True 시 대소문자 무시 비교.

    Returns:
        list of (line_number, matched_literal) — 검출된 모든 사이트.
    """
    target_check: AbstractSet[str]
    if case_insensitive:
        target_check = frozenset(s.lower() for s in target_set)
    else:
        target_check = frozenset(target_set)

    def _matches(value: str) -> str | None:
        check = value.lower() if case_insensitive else value
        if check in target_check:
            return value
        return None

    findings: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        evaluated = static_eval_string(node)
        if evaluated is None:
            continue
        # ast.Constant 의 lineno 직접 사용 (BinOp/JoinedStr 의 lineno 도 동일)
        lineno = getattr(node, 'lineno', 0)
        match = _matches(evaluated)
        if match is not None:
            findings.append((lineno, match))
    return findings


def static_eval_string(node: ast.AST) -> str | None:
    """정적으로 평가 가능한 string 표현이면 결과 반환, 아니면 None.

    공개 SSOT (자기 audit P0-#2 정공, 2026-05-31, cascade-residuals-p2-followup-
    self-audit): 옛 사이트가 `isinstance(node, ast.Constant) and isinstance(
    node.value, str)` ad-hoc 으로 5 우회 (Constant/JoinedStr/BinOp Add/dict-list-
    tuple value/case 변종) 를 검출하지 못하는 결함 정공. AST 가드는 본 helper 에
    위임해야 동등 robustness — 단순 `ast.Constant` 검사 영구 금지.

    검출 대상:
      1. `ast.Constant(str)` (단순 string literal)
      2. `ast.JoinedStr` (f-string) — 모든 part 가 정적 평가 가능한 경우
      3. `ast.BinOp(op=ast.Add())` 양쪽 모두 정적 string 인 경우 (concat)

    dict/list/tuple literal value 내부 nested literal 은 `ast.walk` 가 자식
    `ast.Constant` 노드를 별도 방문하므로 자동 검출 (caller 가 ast.walk 사용 시).

    Args:
        node: AST 노드 (Constant, JoinedStr, BinOp, 그 외).

    Returns:
        정적으로 평가된 string 값, 평가 불가 시 None.

    Example:
        ``ast.parse("'docx not installed'")`` 의 Expression value:
            → Constant → 'docx not installed'
        ``ast.parse("f'docx {version}'")``:
            → JoinedStr 의 FormattedValue 가 비정적 → None
        ``ast.parse("'doc' + 'x'")``:
            → BinOp → 'docx'
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                parts.append(part.value)
            elif isinstance(part, ast.FormattedValue):
                sub = static_eval_string(part.value)
                if sub is None:
                    return None
                parts.append(sub)
            else:
                return None
        return ''.join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = static_eval_string(node.left)
        right = static_eval_string(node.right)
        if left is not None and right is not None:
            return left + right
        return None
    return None


def find_string_literals_in_func_body(
    tree: ast.Module,
    func_names: AbstractSet[str],
    target_set: AbstractSet[str],
    *,
    case_insensitive: bool = False,
) -> list[tuple[str, int, str]]:
    """특정 함수 body 안에서만 string literal 검출.

    Args:
        tree: 모듈 AST.
        func_names: 검사 대상 FunctionDef 이름 집합.
        target_set: 검출할 string literal 집합.
        case_insensitive: True 시 대소문자 무시.

    Returns:
        list of (func_name, line_number, matched_literal).
    """
    results: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in func_names:
            for lineno, lit in find_string_literals_anywhere(
                node, target_set, case_insensitive=case_insensitive,
            ):
                results.append((node.name, lineno, lit))
    return results


def collect_python_files(roots: Iterable, allowed_paths: AbstractSet[str] = frozenset()):
    """Yields Python files under roots, excluding ALLOWED_PATHS."""
    from pathlib import Path
    project_root = Path(__file__).parent.parent
    for root in roots:
        for path in Path(root).rglob('*.py'):
            rel = str(path.relative_to(project_root)).replace('\\', '/')
            if rel in allowed_paths:
                continue
            yield path, rel
