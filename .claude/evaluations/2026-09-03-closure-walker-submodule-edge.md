# 폐포 워커가 서브모듈 import 를 간선으로 세지 않았다 (2026-09-03)

## 어떻게 발견됐나 — **산문과 실측이 어긋난 지점을 추적해서**

2단계 경계를 정하려고 `application` 24파일을 공유/중앙전용으로 갈랐더니
`api_operation_factory.py` 가 **중앙 전용**으로 나왔다. 그런데 그 파일의 패키지
docstring 은 그것을 *"surface-crossing vocabulary"* 라고 적고 있었다.

전에 두 번 밟은 오류(산문을 어휘로 세기)를 생각하면 **산문을 버리는 것이 기본값**
이지만, 이번엔 확인이 한 줄이었다 — provider 트리에 그 파일이 있는가?

    ls /…/FCC_mobile_test_automation/src/application/central_contract/
    → 19파일 전부 있다. api_operation_factory.py 포함.

**「양쪽 트리에 있다」와 「양쪽이 도달한다」는 다른 축**이라 이것만으론 결함이
아니다. 그러나 `api_surfaces.py` 는 **공유 10 안에 있었고**, 그 파일이 표면 9개를
정적으로 import 한다. 도달 가능한 것이 도달 목록에 없다 — 여기서 결함이 확정됐다.

## 결함

`scripts/check_shared_kernel_closure.py::_imports`

```python
if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
    out.add(node.module)          # ← 패키지만. 서브모듈 이름을 버린다
```

`from application.central_contract import surface_auth, …` 는
`application.central_contract` 만 담기고, 그것은 그 패키지의 `__init__.py` 로
해소된다. **그 파일은 순수 docstring 43줄이라 탐색이 거기서 멈춘다.**

⚠️ **빈 `__init__.py` 가 이 결함의 조건이었다.** 그것이 무언가를 재수출했다면
그 import 문이 우연히 간선을 만들어 결함이 가려졌을 것이다. 그래서 봉인의
해당 팔도 **빈 `__init__`** 으로 구성한다.

## 축 맹점의 어느 형태인가

> AST 축에서 **「import 안 함」과 「`from pkg import submodule` 로 import 함」이
> 같은 값**이다.

그리고 **틀리는 방향이 나쁜 쪽이다.** ADR-0001 의 완료 오라클이 *「공유 폐포 0」*
인데, 과소계수하는 워커는 **아직 공유 중인데도 0** 을 낸다. 즉 이 결함은
이관이 끝났다고 **잘못 선언하게** 만든다.

## 고친 뒤 — 43 → 54

놓쳤던 11개:

    직접(9)   central_contract 의 surface_auth · surface_samples · surface_reports ·
              surface_artifact_custody · surface_reference_catalog ·
              surface_project_results · surface_chambers · surface_providers ·
              surface_projects        ← 전부 api_surfaces.py 가 부른다
    추이(2)   api_operation_factory        ← 표면 6개가 부른다
              domain/services/reference_entry_edit_policy  ← surface_reference_catalog 가 부른다

## 기준선을 올린 것이 「게이트 끄기」가 아님의 증명

게이트 자신이 경고한다 — *"기준선을 관측값으로 덮어써서 초록을 만들지 마라."*
그 경고가 겨냥하는 것은 **코드가 회귀했는데 기준선을 따라 올리는 것**이다.
여기는 그 경우가 아니고, 그것을 **기계적으로** 보였다:

1. 이 커밋이 만진 파일은 워커와 그 봉인 **둘뿐**이다.
   `application/`·`domain/`·`infrastructure/` 변경 **0건** (`git diff --name-only`).
2. 새 11개가 전부 새로 도달된 표면에서 도달한다 — 직접 9는 import 문을 지목했고,
   추이 2는 부르는 파일을 지목했다.

즉 **트리는 그대로이고 자(尺)가 정확해졌다.**

## 봉인 — 양방향

`tests/test_shared_kernel_closure.py::TestSubmoduleImportsAreEdgesToo`

    test_a_submodule_imported_from_its_package_is_in_the_closure   과소계수를 막는다
    test_an_attribute_import_is_not_mistaken_for_a_submodule       과잉계수를 막는다

⚠️ **뒤쪽 팔이 없으면 「간선을 더 담아라」가 「아무 이름이나 담아라」로 퇴화**하고,
그때 폐포가 부풀어 완료 오라클이 영영 0 이 되지 않는다. 속성 import 를 모듈로
오인하지 않는 것은 `_resolve` 의 **파일 존재** 판정이 맡는다 — 이름 모양이 아니다.

고치기 전에 red 를 확인했다(`assertIn … not found in [facade.py, pkg/__init__.py]`).

## 술어 없는 개수를 이 세션에서 세 번째로 밟았다

| 세었던 것 | 두 답 | 왜 둘 다 맞나 |
|---|---|---|
| 공유 커널 파일 | 15 / 53 | 「내가 복사한 집합」 vs 「폐포」 |
| 스위트 실패 | 22 / 17 | pytest subtest 개별 vs lane_check 부모 node-id |
| `application` 재작성 | 198 / 102 | 모듈별 건수의 합(중복) vs 이름을 언급하는 import 줄 |

세 번 다 **두 수가 모두 정확했고 술어만 달랐다.** 그래서 ADR-0001 에는 수만
적지 않고 **무엇을 센 수인지**를 함께 적었다.

## 파장 — ADR-0001 을 정정했다

| | 처음 | 정정 |
|---|---|---|
| 공유 폐포 | 53 | **64** |
| 중앙만 | 47 | **49** |
| provider만 | 282 | **291** |
| 1단계 후 | 43 | **54** |

옮긴 파일 수 10 은 변하지 않는다 — 1단계 자체는 옳았다.
