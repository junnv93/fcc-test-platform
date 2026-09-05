"""Stage multi-repo extraction packages and emit cutover extraction evidence.

⚠️ 이것은 `scripts/platform_extraction_runner.py` 의 **알맹이**다. `scripts/` 는 패키지가 아니라
**휠이 나르지 못한다** — 이 레인을 핀으로 받는 소비자에게 그 파일은 오지 않는다.
그래서 로직은 여기 살고 `scripts/` 에는 진입점만 남는다. 껍데기는 양쪽 레포에
두되, 담긴 것이 그뿐이라 **갈라질 것이 없다.**
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys


# ⚠️ **모듈 위치에서 파생하면 안 된다** — 이 저장소가 선례에서 두 번 값을 치렀다
# (모노레포 장부 `tech-debt-tracker.md:7361`): `parents[2]` 는 한 칸 지나쳤고
# `parents[1]` 은 **설치된 자리에서 `site-packages`** 였다. 축이 틀렸던 것 — 이
# 모듈은 「자기가 어디 있나」가 아니라 **「지금 어느 저장소를 다루나」**를 알아야
# 하고 그건 **호출자**가 정한다. `scripts/` 에 있을 때는 두 답이 우연히 같았지만,
# 휠이 이 모듈을 나르기 시작하면 갈라진다.
def _repository_root() -> Path:
    """대상 저장소 = 호출자의 작업 디렉터리(또는 그 조상 중 첫 저장소).

    ⚠️ 찾지 못하면 **조용히 계속하지 않는다** — 아래가 전부 이 뿌리 위에서 파일을
    세므로, 틀린 뿌리는 「대상이 없다」로 조용히 답하고 그 모양은 「경로가 맞다」와
    구별되지 않는다.
    """
    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if (candidate / 'pyproject.toml').is_file() and (candidate / '.git').exists():
            return candidate
    raise RuntimeError(
        f'대상 저장소를 찾지 못했다 (cwd={here}) — 이 도구는 저장소 안에서 실행해야 '
        '한다. 모듈이 사는 곳이 아니라 **다루는 곳**이 기준이다.'
    )


PROJECT_ROOT = _repository_root()
from fcc_test_platform.extraction_evidence import (  # noqa: E402
    extraction_package_errors,
    extraction_target_lanes,
)
from fcc_test_contracts.common.extraction_lane_policy import ExtractionLanePolicy  # noqa: E402
from fcc_test_contracts.common.tree_artifacts import discover_tree_artifact  # noqa: E402
# ⚠️ **형제 스크립트 이름이 아니라 배포되는 패키지 경로로 부른다** (2026-09-05).
#
# 직전까지 이 자리는 `from check_extraction_import_boundaries import ...` 였다. 그 두
# 이름은 계약 레인의 `scripts/` 에 살고, 그 디렉터리는 **휠이 나르지 못한다** — 그래서
# 이 러너는 배송된 상자에서 `ModuleNotFoundError` 로 즉사했고, 그런데
# `fcc_test_platform/cutover_workflow_hints.py` 는 운영자에게 그 명령을 안내했다.
# 공급 폐포 게이트(`tests/test_supply_closure_axis.py`)가 그 둘을 계급 B 로 이름 댔다.
#
# 모노레포 매니페스트가 옛 답을 적어 뒀다 — *"형제의 `scripts/` 를 PYTHONPATH 에 얹어라.
# platform conftest 는 자기 것만 얹을 수 있다."* 그것은 **스테이징 시점의 우회**이고,
# 형제 디렉터리가 없는 배송 상자에서는 성립하지 않는다. 계약 레인이 알맹이를 배포되는
# 패키지로 올렸고(`fcc-test-contracts` v0.1.19), 이제 휠이 그것을 나른다.
from fcc_test_contracts.extraction_import_boundaries import (  # noqa: E402
    check_dependency_resolution,
    check_import_boundaries,
)
from fcc_test_contracts.extraction_package import (  # noqa: E402
    build_extraction_plan,
    stage_extraction_package,
)

DEFAULT_MANIFEST = discover_tree_artifact(
    __file__, 'docs', 'api', 'headless_contract_extraction_manifest.v1.json',
)

#: Governance key holding the per-lane ceiling on staged import violations.
STAGED_IMPORT_VIOLATION_BASELINE = 'staged_import_violation_baseline'

#: Governance key holding the per-lane ceiling on *permitted but undelivered*
#: imports. A separate ceiling from the one above because it is a separate
#: defect: the first is a tree reaching somewhere it may not, the second is a
#: tree reaching somewhere it may — into a package nobody put in the box.
STAGED_UNRESOLVED_DEPENDENCY_BASELINE = 'staged_unresolved_dependency_baseline'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Stage extraction packages and create extraction evidence.')
    parser.add_argument('--manifest', type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument('--target-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--evidence-id', required=True)
    parser.add_argument('--collected-at', default='')
    parser.add_argument('--extracted-at', default='')
    # One repeatable flag instead of one flag per lane. The old
    # --contracts-ref/--platform-ref pair had to grow by hand every time a lane
    # appeared, which is half of what kept the chamber node runtime waiting on a
    # policy that already handled N lanes.
    parser.add_argument(
        '--target-ref', action='append', default=[], metavar='LANE=REF',
        help='target ref for one extraction lane; repeat once per lane',
    )
    parser.add_argument('--require-valid', action='store_true')
    args = parser.parse_args(argv)

    args.output.parent.mkdir(parents=True, exist_ok=True)

    try:
        extraction_manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
        target_refs = _parse_target_refs(args.target_ref, extraction_manifest)
        manifest = build_extraction_evidence(
            manifest_path=args.manifest,
            extraction_manifest=extraction_manifest,
            target_root=args.target_root,
            evidence_id=args.evidence_id,
            collected_at=args.collected_at or datetime.now(timezone.utc).isoformat(),
            extracted_at=args.extracted_at or datetime.now(timezone.utc).isoformat(),
            target_refs=target_refs,
        )
    except Exception as exc:
        print(json.dumps({'staged': False, 'valid': False, 'error': str(exc)}, sort_keys=True, indent=2), file=sys.stderr)
        return 2

    args.output.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n', encoding='utf-8')

    # The boundary findings live in the evidence document itself, so a stored
    # record can never claim validity over a tree that does not import. Merged
    # here with the schema errors, most specific first.
    issues = list(manifest.get('issues') or []) + [
        issue.to_dict()
        for issue in extraction_package_errors(manifest, extraction_manifest=extraction_manifest)
    ]
    staged = all(
        repo.get('extracted') is True
        for repo in (manifest.get('repositories') or {}).values()
    )
    print(json.dumps({'staged': staged, 'valid': not issues, 'issues': issues}, sort_keys=True, indent=2))
    if args.require_valid and issues:
        return 1
    return 0 if manifest['compatible'] else 1


def _parse_target_refs(raw: list[str], extraction_manifest: dict) -> dict[str, str]:
    """Resolve ``LANE=REF`` pairs against the lanes the manifest actually extracts.

    Both directions are errors, and both used to be impossible to express: a ref
    for a lane that is not a target is a typo the old fixed flags could not even
    represent, and a target with no ref would previously have raised ``KeyError``
    deep inside evidence assembly rather than saying which lane was missing.
    """
    lanes = extraction_target_lanes(extraction_manifest)
    refs: dict[str, str] = {}
    for item in raw:
        lane, separator, ref = str(item).partition('=')
        if not separator or not lane.strip() or not ref.strip():
            raise ValueError(f'--target-ref must be LANE=REF, got {item!r}')
        refs[lane.strip()] = ref.strip()

    unknown = sorted(set(refs) - set(lanes))
    if unknown:
        raise ValueError(
            f'--target-ref names lanes that are not extraction targets: '
            f'{", ".join(unknown)} (targets: {", ".join(lanes)})'
        )
    missing = [lane for lane in lanes if lane not in refs]
    if missing:
        raise ValueError(
            f'--target-ref missing for extraction target(s): {", ".join(missing)}'
        )
    return refs


def build_extraction_evidence(
    *,
    manifest_path: Path,
    extraction_manifest: dict,
    target_root: Path,
    evidence_id: str,
    collected_at: str,
    extracted_at: str,
    target_refs: dict[str, str],
) -> dict:
    target_lanes = extraction_target_lanes(extraction_manifest)
    plans = {
        repo_name: build_extraction_plan(manifest_path=manifest_path, repository=repo_name)
        for repo_name in target_lanes
    }
    issues = [
        issue
        for plan in plans.values()
        for issue in plan.get('issues', [])
    ]
    if issues:
        return _manifest(
            evidence_id=evidence_id,
            collected_at=collected_at,
            manifest_path=_relative(manifest_path),
            compatible=False,
            issues=issues,
            repositories={},
        )

    staged_records: dict[tuple[str, str, str], dict] = {}
    for plan in plans.values():
        for record in stage_extraction_package(plan, target_root):
            staged_records[(record['repository'], record['source'], _relative_to_target(record['destination'], target_root, record['repository']))] = record

    # Derived from the manifest this run was given, not from the repository
    # default: a gate that judges a staged tree by a *different* document than
    # the one that staged it is answering about a package nobody built.
    #
    # Bound to PROJECT_ROOT for the same reason check_extraction_import_
    # boundaries.load_policy() already binds: "who owns scripts.foo" is a
    # monorepo ownership question, answered by entry_point_module_index, and
    # an unbound policy publishes no entry-point identities at all. Left
    # unbound, this runner could not see a bare scripts/ import cross a lane
    # boundary regardless of how many violations check_import_boundaries()
    # found through the (bound) CLI — the exact discrepancy
    # scripts-namespace-blind-axis's baseline re-measurement surfaced:
    # staged_import_violation_baseline['fcc-test-platform'] moved 24 -> 25 by
    # a real crossing (scripts/provider_service_deployment_evidence.py ->
    # scripts.headless_api_service) that only the bound checker could see,
    # while this runner kept silently reporting 24 either way.
    policy = ExtractionLanePolicy.from_manifest(extraction_manifest).bound_to(PROJECT_ROOT)
    repositories = {}
    boundary_issues: list[dict] = []
    for repo_name in target_lanes:
        evidence = _repository_evidence(
            repo_name=repo_name,
            plan=plans[repo_name],
            target_root=target_root,
            target_ref=target_refs[repo_name],
            extracted_at=extracted_at,
            staged_records=staged_records,
        )
        boundary, issues_for_lane = _import_boundary_evidence(
            repo_name=repo_name,
            staged_root=target_root / repo_name,
            extraction_manifest=extraction_manifest,
            policy=policy,
        )
        evidence['import_boundary'] = boundary
        boundary_issues.extend(issues_for_lane)
        resolution, resolution_issues = _dependency_resolution_evidence(
            repo_name=repo_name,
            staged_root=target_root / repo_name,
            extraction_manifest=extraction_manifest,
            policy=policy,
            target_root=target_root,
        )
        evidence['dependency_resolution'] = resolution
        boundary_issues.extend(resolution_issues)
        repositories[repo_name] = evidence

    return _manifest(
        evidence_id=evidence_id,
        collected_at=collected_at,
        manifest_path=_relative(manifest_path),
        compatible=not boundary_issues,
        issues=boundary_issues,
        repositories=repositories,
    )


def _import_boundary_evidence(
    *, repo_name: str, staged_root: Path, extraction_manifest: dict, policy,
) -> tuple[dict, list[dict]]:
    """Run the lane's import gate over the tree that was just staged.

    Until now this runner answered ``valid: true`` without ever asking. It
    validated the *shape* of its own evidence — paths safe, hashes matching,
    every declared row present — and called that validity. Meanwhile the staged
    platform tree imported ``application.common.access_policy`` and
    ``application.headless.api_contracts``, both of which had already moved to
    the contracts lane, so the delivered package reached for names that would
    not exist. The check existed; nothing called it.

    The ceiling is declared in the manifest, not inferred here: a runner that
    computes its own tolerance can always be satisfied. Same discipline as
    ``cross_lane_import_baseline`` — ratchet down only, and a key that reaches
    zero stays in the document so the pair it closed remains visible.
    """
    baseline_map = (
        (extraction_manifest.get('governance') or {}).get(STAGED_IMPORT_VIOLATION_BASELINE) or {}
    )
    payload = check_import_boundaries(staged_root, lane=repo_name, policy=policy)
    violations = payload.get('violations') or []
    baseline = int(baseline_map.get(repo_name, 0))

    issues: list[dict] = []
    if len(violations) > baseline:
        issues.append({
            'code': 'staged_import_boundary_regression',
            'path': f'repositories.{repo_name}.import_boundary',
            'message': (
                f'{len(violations)} staged import violations exceed the declared '
                f'baseline of {baseline}: '
                + ', '.join(
                    f'{item["path"]}:{item["line"]} {item["module"]}'
                    for item in violations[:5]
                )
            ),
        })
    return (
        {
            'lane': repo_name,
            'python_files': payload.get('python_files', 0),
            'violation_count': len(violations),
            'baseline': baseline,
            # Carried even when the count is within budget. A gate that only
            # speaks when it fails leaves "we checked and found four" and "we
            # never looked" indistinguishable in the record.
            'violations': violations,
        },
        issues,
    )


def _sibling_delivery_roots(
    *, repo_name: str, extraction_manifest: dict, policy, target_root: Path,
) -> dict[str, Path]:
    """Lanes delivered alongside ``repo_name`` in this same run.

    Derived, not declared: a dependency is installable-from-a-sibling exactly
    when the lane is one this lane ``depends_on``, has a package name, and is an
    extraction target. Adding a manifest key for it would restate three facts
    the manifest already carries, and restated facts are where the next drift
    starts (the rule that removed the hardcoded lane triples says so by name).

    Returns paths whether or not they exist — ``check_dependency_resolution``
    credits only the ones that do, so a lane the operator did not stage is
    reported as unresolved rather than assumed present.
    """
    targets = set(extraction_target_lanes(extraction_manifest))
    package_names = extraction_manifest.get('package_names') or {}
    return {
        sibling: target_root / sibling
        for sibling in policy.depends_on.get(repo_name, ())
        if sibling in targets and sibling in package_names and sibling != repo_name
    }


def _dependency_resolution_evidence(
    *, repo_name: str, staged_root: Path, extraction_manifest: dict, policy,
    target_root: Path,
) -> tuple[dict, list[dict]]:
    """Record what the staged tree may import but will not find.

    Kept as its own axis rather than folded into the boundary count, because
    collapsing them would make the two indistinguishable in the record — and
    they call for opposite repairs. A boundary violation is fixed by *removing*
    an import; an unresolved dependency is fixed by *delivering* one.

    The platform package is the worked example: 25 boundary violations against a
    ceiling of 25 read as a clean run, while 26 imports of ``fcc_test_contracts``
    and 11 of ``domain`` — both permitted by ``depends_on`` — resolved nowhere,
    so 40 of its 78 shipped test files could not be collected.
    """
    baseline_map = (
        (extraction_manifest.get('governance') or {}).get(
            STAGED_UNRESOLVED_DEPENDENCY_BASELINE
        ) or {}
    )
    sibling_roots = _sibling_delivery_roots(
        repo_name=repo_name,
        extraction_manifest=extraction_manifest,
        policy=policy,
        target_root=target_root,
    )
    payload = check_dependency_resolution(
        staged_root, lane=repo_name, policy=policy, sibling_roots=sibling_roots,
    )
    unresolved = payload.get('unresolved') or []
    baseline = int(baseline_map.get(repo_name, 0))

    issues: list[dict] = []
    if len(unresolved) > baseline:
        issues.append({
            'code': 'staged_dependency_resolution_regression',
            'path': f'repositories.{repo_name}.dependency_resolution',
            'message': (
                f'{len(unresolved)} permitted imports resolve nowhere in the staged '
                f'tree, exceeding the declared baseline of {baseline}: '
                + ', '.join(item['module'] for item in unresolved[:5])
            ),
        })
    return (
        {
            'lane': repo_name,
            'python_files': payload.get('python_files', 0),
            'unresolved_count': len(unresolved),
            'baseline': baseline,
            'unresolved': unresolved,
            # Two mechanisms, kept apart in the record. Names resolved because
            # the files are in the box do not appear here at all; these are the
            # ones a receiving team must install beside it. Folding them into
            # one count would say a package is runnable without saying what it
            # takes to run it.
            'satisfied_by_sibling': payload.get('satisfied_by_sibling') or [],
        },
        issues,
    )


def _repository_evidence(
    *,
    repo_name: str,
    plan: dict,
    target_root: Path,
    target_ref: str,
    extracted_at: str,
    staged_records: dict[tuple[str, str, str], dict],
) -> dict:
    entries = []
    for entry in plan['packages'][repo_name]:
        source = PROJECT_ROOT / entry['current_path']
        destination = (target_root / repo_name / entry['future_path']).resolve(strict=False)
        source_sha = _sha256_file(source)
        destination_sha = _sha256_file(destination)
        evidence_entry = {
            'current_path': entry['current_path'],
            'future_path': entry['future_path'],
            'kind': entry['kind'],
            'byte_size': source.stat().st_size,
            'source_sha256': source_sha,
            'destination_sha256': destination_sha,
            'copied': destination.is_file(),
        }
        staged = staged_records.get((repo_name, entry['current_path'], entry['future_path'])) or {}
        import_rewrites = staged.get('import_rewrites') or []
        if import_rewrites:
            evidence_entry['transforms'] = [{
                'type': 'python_import_rewrite',
                'count': len(import_rewrites),
            }]
        entries.append(evidence_entry)
    return {
        'target_repository': repo_name,
        'target_ref': target_ref,
        'extracted_at': extracted_at,
        'package_compatible': True,
        'extracted': True,
        'entries': entries,
    }


def _manifest(
    *,
    evidence_id: str,
    collected_at: str,
    manifest_path: str,
    compatible: bool,
    issues: list,
    repositories: dict,
) -> dict:
    return {
        'schema_version': 1,
        'evidence_id': evidence_id,
        'collected_at': collected_at,
        'manifest_path': manifest_path,
        'compatible': compatible,
        'issues': issues,
        'repositories': repositories,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def _relative_to_target(destination: str, target_root: Path, repo_name: str) -> str:
    try:
        return Path(destination).resolve(strict=False).relative_to(
            (target_root / repo_name).resolve(strict=False),
        ).as_posix()
    except ValueError:
        return ''


if __name__ == '__main__':
    raise SystemExit(main())
