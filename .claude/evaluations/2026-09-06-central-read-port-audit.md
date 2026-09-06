# central read 어댑터 10모듈 strict — **포트 감사 보고** (2026-09-06)

PR #115(write 어댑터 11모듈)의 «대칭»이다. 산출물은 「선언 13건」이 아니라 **포트 감사
보고**이고, 이번에는 그 감사가 예상 밖의 것을 하나 찾았다: **이 게이트가 볼 수 없는 축**.

---

## 실측 0 — 착수 근거의 수는 «오류 코드로 나눠야» 재현된다

착수 근거는 read 어댑터 10개의 strict 비용을 **13건**(7개는 0건)이라 적었다. 켜 보니
**25건**이 나왔다. 두 수 다 맞다 — 세는 것이 다르다:

| | no-untyped-def | attr-defined | 합 |
|---|---:|---:|---:|
| artifact_custody | 8 | 1 | 9 |
| sample_inventory | 4 | 3 | 7 |
| progress_catalog | 1 | 1 | 2 |
| chamber · progress · project · rbac · reference · report · test_equipment_list | 0 (각) | 1 (각) | 7 |
| **합** | **13** | **12** | **25** |

착수 근거의 13은 `mypy -p …application` 전체를 돌려 **「선언 누락」만** 센 수다(그 층의
잔여 145건이 전부 그 코드였다). strict 를 «모듈 단위»로 켜면 두 번째 계급이 함께 나온다.

⚠️ **그 12건이 이 웨이브의 본체다.** 착수 근거가 ②에서 예고한 그대로 — 타입을 적는
순간 계약이 검사된다.

---

## 실측 1 — **게이트가 커널 타입을 못 본다** (이 웨이브의 최대 발견)

12건은 전부 커널 DB 표면을 어긴 것이다:

```
"DbCursor" has no attribute "fetchall"     10건  (10모듈 중 10)
"DbCursor" has no attribute "fetchone"      1건  (sample_inventory:317)
"DbConnection" has no attribute "close"     1건  (artifact_custody:206)
```

그런데 **CI 도 pre-push 도 이 12건을 보지 않는다.** rig 둘로 갈랐다 — 같은 커밋,
같은 mypy 2.3.1, `MYPYPATH` 만 다르다:

| rig | `MYPYPATH` | read 10모듈 |
|---|---|---:|
| A (게이트가 쓰는 것) | `REPO_ROOT` | **13건** |
| B (커널 타입 해소) | `REPO_ROOT:형제트리:커널트리` | **25건** |

원인은 **PEP 561** 이다. `fcc_test_kernel` 도 `fcc_test_contracts` 도 `py.typed` 를
싣지 않는다(실측: 두 트리 전수 `find -name py.typed` → **0건**). mypy 는 그런 설치본의
타입을 쓰지 않고, `mypy.ini` 의 `ignore_missing_imports = True` 가 그 부재를 조용히
`Any` 로 바꾼다. 그래서 커널 포트를 어긴 자리가 **초록**이다.

`tests/test_architecture_gate_conformance.py::_tool_env` 는 `PYTHONPATH` 에 형제 레인과
커널을 «넣는다». 그것은 import-linter 에는 답이지만 **mypy 에는 아니다** — mypy 는
`PYTHONPATH` 가 아니라 `MYPYPATH` 와 실행 인터프리터의 site-packages 로 모듈을 찾고,
같은 함수가 `MYPYPATH` 는 `REPO_ROOT` «하나»로 둔다. 즉 게이트는 커널 계약을 검사한다고
믿으면서 검사하지 않는다.

⚠️ **미릴리스 변경이 딸려 온 것이 아니다.** 형제 트리의
`platform_database_port.py` 와 `central_rbac_read_port.py` 를 핀 태그
`kernel-v0.5.0` 과 비교했다 — `git diff` 공집합. **핀된 표면**을 어기고 있었다.

### 이 웨이브가 한 처분

12건을 **전부** 없앴다. read 어댑터 10모듈이 커널 `DbCursor`/`DbConnection` 대신 이
레인의 `RowCursor`/`RowConnection`(`central_db_surfaces.py` — PR #115 가 만든 그 한
자리)을 받는다. 사본을 뜨지 않았다.

    rig A: 25 → 0        rig B: 25 → 0

게이트 수치는 안 변한다(A 에서는 애초에 13이었다). **커널이 `py.typed` 를 얻는 날
값을 낸다.**

### 이 웨이브가 «하지 않은» 처분 — 다음 사람에게 넘기는 수

같은 rig B 로 **이미 strict 인 15개 전부**를 다시 쟀다:

| 대상 | rig A | rig B |
|---|---:|---:|
| `domain` · `infrastructure` · `application.session` · `application.headless` | 0 | 0 |
| write 어댑터 11모듈 (PR #115) | 0 | **50** |
| read 어댑터 10모듈 (이 PR, 처분 후) | 0 | 0 |

write 50건의 분해: reference 19 · artifact_custody 5 · project 4 · claim 4 ·
membership 4 · chamber 3 · test_equipment_list 3 · user 3 · report 3 ·
sample_inventory 1 · progress 1.

그중 하나는 이름을 대 둘 가치가 있다. `central_artifact_custody_write_adapter.py:277`
의 `_close(connection: RowConnection)` 이 `connection.close()` 를 **직접** 부른다.
`central_db_surfaces.py` 는 *"연결을 닫는 자리는 한 군데뿐이고 거기서도 `getattr` 로
「있으면 닫는」다"* 고 적는데, 실측은 write 어댑터 10자리 중 **아홉이 `getattr`,
하나가 직접**이다. 그 하나가 자기 문서와 어긋난다. read 쪽 같은 자리
(`artifact_custody_read:206`)는 이 PR 이 `getattr` 형태로 맞췄다 — 다수파를 따랐고,
`RowConnection` 의 「`close` 는 선택 표면」이라는 판정을 지켰다.

⚠️ **이 축을 이번에 켜지 않은 이유.** 커널에 `py.typed` 를 넣는 것은 **다른 레포**의
태그 작업이고, `MYPYPATH` 에 형제 트리를 더하는 것은 «개발자 체크아웃에서만» 참인
길이다(CI 러너는 형제 트리가 없다 — `_contracts_are_reachable()` 이 같은 값을 이미
치렀다). 그러면 게이트가 기계마다 다른 답을 낸다. 올바른 처분은 커널이 `py.typed`
를 싣는 것이고, 그날 이 표의 50건이 한꺼번에 나타난다.

---

## 실측 2 — read 포트는 **두 패키지**에 흩어져 산다

| | platform `domain/ports/output` | kernel `domain/ports/output` |
|---|---:|---:|
| `*WritePort` | 12 | **0** |
| `*ReadPort` | 10 | **1** (`CentralRbacReadPort`) |

PR #115 의 write 봉인이 이 레포의 포트 패키지 하나만 훑는 것은 **옳다** — write 포트는
거기 전부 있다. read 는 아니다. `PostgresCentralRbacReadAdapter` 가 구현한다고 이름으로
말하는 포트는 **커널에** 있고, 포트 패키지 하나만 훑는 봉인은 그 짝을 조용히 빠뜨린다.

그리고 모듈 이름은 규약이 아니다. read 포트 10개(platform) 중 **다섯**이
`central_<X>_read_port.py` 가 아닌 모듈에 산다 — artifact_custody · project ·
reference · report · test_equipment_list 는 `central_<X>_port.py` 다. write 에서
5/12 를 놓칠 뻔한 것과 **같은 형태**다. 짝은 클래스 이름에서 파생했다.

---

## 실측 3 — 메서드 집합과 **반환 타입** (감사의 본체)

11쌍 전부를 두 축으로 대조했다:

| 축 | 결과 |
|---|---|
| 포트가 약속한 메서드가 어댑터에 있는가 | **11/11 · 갭 0건** |
| 어댑터의 반환 타입 «표기»가 포트와 같은가 | **11/11 · 갈라짐 0건** (약속 메서드 38개 전수) |
| 약속 0개인 포트 | 0건 |
| 어댑터가 자기 포트 이름을 코드에서 대는가 | **10/11** — 아래 |

⚠️ 착수 근거는 *"Mapping 을 약속하고 원시 튜플을 주는 자리가 있는지 물어라"* 고 적었다.
**없다.** read 어댑터군은 이 축에서 이미 규율돼 있었다 — 그것도 실측 결과이고, 봉인이
그 상태를 지킨다(`tests/test_read_port_conformance.py`).

### 어댑터가 포트 이름을 «한 번도 대지 않는» 자리 — read 에서도 정확히 하나

    central_sample_inventory_read_adapter  →  CentralSampleInventoryReadPort  언급 0건

write 에서 `CentralSampleInventoryWritePort` 가 같았다. **같은 도메인이 양쪽에서 같은
형태를 낸다.** 오늘은 여덟 메서드가 구조적으로 맞아 돌지만 둘을 잇는 것이 코드에 없다.

⚠️ 이번에는 그것을 **문서가 아니라 봉인**으로 옮겼다.
`PORTS_NOT_NAMED_BY_THEIR_ADAPTER` 는 **집합 등호**다 — 늘면 red, 줄여도 red(선언을
같이 지우라는 뜻). 「N개 이하」로 두면 내일 하나가 더 생겨도 조용하다.

---

## 봉인 — `tests/test_read_port_conformance.py` (새 파일)

### write 봉인과 «합치지 않은» 사유

합치면 대조 규칙이 하나가 된다. 그런데 **대상이 다르다**:

1. read 는 포트 색인이 **두 패키지**를 돌아야 한다(실측 2). 한 파일에서 처리하려면
   write 의 훑기도 넓어지고, 그러면 커널에 `…WritePort` 가 생기는 날 **write 판정이
   아무도 결정하지 않은 채로** 바뀐다.
2. read 에는 **반환 타입 축**이 있다(실측 3). write 에 걸 이유가 없다.

⚠️ 그래도 **메서드 집합 규칙 자체는 한 곳**이다 — 새 파일이 write 봉인에서
`_promised` 를 import 한다. 두 파일이 각자 「약속이란 무엇인가」를 정의하면 그중
하나가 먼저 낡는다. write 파일이 옮겨지면 이 import 가 이름을 대며 멈추고, 그때
옮긴 사람이 두 봉인을 같이 본다.

### 색인은 디렉터리 글롭이 아니라 `pkgutil`

커널은 형제 «트리»일 수도, CI 처럼 pip 설치본일 수도 있다(그리고 실측상 namespace
패키지라 `__path__` 에 둘이 동시에 들어온다). `REPO_ROOT.parent` 로 경로를 지으면
CI 에서 조용히 0개가 되고 rbac 짝이 사라진다. `pkgutil.iter_modules(pkg.__path__)` 는
«임포트가 답한 자리»를 훑으므로 둘 다 답한다.

### 주입 확인 — 네 팔 전부 (실측)

| 주입 | 결과 |
|---|---|
| 포트에만 있는 메서드 `read_chamber_ghost` | red — `central_chamber_read_adapter` **와** `read_chamber_ghost` 둘 다 출력 |
| 어댑터 반환을 `list[dict]` → `list` | red — `…read_chamber_nodes: 포트=list[dict] 어댑터=list` |
| sample_inventory 가 포트 이름을 대게 함 | red — `선언: [...] 실측: []` |
| `CentralReportReadPort` 를 개명 | red — `central_report_read_adapter: CentralReportReadPort 를 못 찾았다` |

그리고 strict 장부 쪽도 주입으로 확인했다 — **비용 0건이던** `rbac` 에 선언 없는 함수를
넣자 `test_the_strict_layers_have_no_untyped_defs` 가 그 모듈 이름을 대며 red 가 됐다.
0건인 일곱을 「켤 필요 없다」고 넘겼다면 그 red 는 오늘도 내일도 안 났을 것이다.

---

## 포트 감사 진도 — **33개 중 22개**

`fcc_test_platform/domain/ports/output`: 파일 26 · Protocol **33** (PR #115 의 분모를
그대로 재현했다). 감사된 것: write 12 + read 10 = **22**.

⚠️ **분모 자체가 좁다.** `CentralRbacReadPort` 는 이 웨이브가 감사했지만 그 33 «밖»에
있다 — 커널 패키지에 산다. 두 패키지를 합치면 Protocol 은 **42개**이고
(platform 33 + kernel 9), 감사된 것은 **23**이다. 커널 쪽 9에는
`DbCursor`·`DbConnection` 이 포함되며, 이 웨이브가 실측 1 에서 다룬 것이 정확히 그 둘이다.

### 잔여 11 (33 기준) — «어느 어댑터에 매달려 있는가»

| 포트 | 매달린 자리 |
|---|---|
| `CentralResultSelectionPort` | `central_result_selection_adapter` · `…_service` |
| `CentralRekeyIngestPort` · `RekeyMappingEnvelopeLike` | `central_rekey_ingest_adapter` · `…_evidence` |
| `CentralProjectReferencePort` | `central_project_reference_adapter` · `…_service` |
| `ChamberMeasurementProxyPort` | `chamber_proxy_adapter` · `chamber_measurement_service` |
| `ChamberProgressBroadcastPort` | `chamber_progress_broadcaster` · `api/platform_routes` |
| `CentralIdResolverPort` | `progress_expectation_sync_service` |
| `ProjectResultReferenceProviderPort` | `central_project_reference_service` |
| `SampleInventoryExportPort` | `sample_inventory_export_service` |
| `PlatformIngestionTransaction` · `PlatformIngestionWriter` | **없음** — 아래 |

⚠️ **구현도 언급도 0건인 포트가 둘이다.** `platform_ingestion_port.py` 의 두 Protocol 은
`application` · `infrastructure` · `api` 어디에서도 이름이 나오지 않는다(grep 실측 —
줄 단위 스캔이므로 이것은 **하한**이다). 아무도 만족하지 않는 포트는 「만족한다」가
공허하고, 그 부재는 어느 검사도 묻지 않는다.

### 다음 웨이브에 좋은 순서

착수 근거의 「기타 어댑터 3개 16건」이 잔여 포트와 겹친다 —
`central_result_selection_adapter`(9건) · `central_rekey_ingest_adapter`(6건) ·
`published_plan_identity_adapter`(1건). 그 셋을 켜면 잔여 11 중 **셋**이 감사되고
어댑터 계열이 완주한다.

---

## 이 웨이브가 켜지 «않은» 모듈 하나

`central_read_adapter.py` 는 strict 대상에 없다. 이름이
`central_<X>_read_adapter` 규약을 따르지 않아 `STRICT_SECTIONS` 의 파생이 만들 수 없고,
손으로 한 줄 더하면 파생과 열거가 한 파일에서 섞인다. 비용은 실측 0건이므로 다음
웨이브가 「나머지 application」을 자를 때 함께 들어간다.

⚠️ 다만 **포트 봉인은 이 모듈도 덮는다** — 봉인의 짝짓기는 장부와 독립이고
`central_*read_adapter.py` 를 전부 본다. 그래서 감사된 read 포트는 10이 아니라 **11**이다.

---

## 재현

### lane_check — 대조군과 «이름 집합» 동일

rig 를 고정하고(공유 `.venv` + 형제 레인 `PYTHONPATH` + yaml·mypy·import-linter)
베이스 커밋 `3ff6626` 과 이 브랜치를 같은 명령으로 돌렸다:

| | 관측된 실패 이름 | passed | skipped | subtests |
|---|---|---:|---:|---:|
| 베이스 `3ff6626` | **9개** | 3,202 | **27** | 843 |
| 이 브랜치 | **같은 9개** | 3,209 | **27** | 855 |

`lane_check` exit **0** (선언된 실패 0 = 관측된 실패 0 — 위 9개는 `lane_check` 가
아니라 이 rig 가 만든 것이고 베이스에도 그대로 있다). 증감이 전부 설명된다:
passed +7 = 새 봉인의 7시험, subtests +12 = strict 대상 15→25(+10) + `PORT_PACKAGES`
2, **skipped 27 로 불변**(여섯 웨이브 내내 27).

⚠️ 그 9개 중 둘은 **이 rig 가 만든 것**이다.
`test_the_strict_layers_have_no_untyped_defs` 와 `test_the_three_contracts_hold` 는
mypy·import-linter 를 `PYTHONPATH` 로만 도달시킨 탓에 red 다 — 두 검사는 자식
프로세스를 띄우면서 `_tool_env()` 로 `PYTHONPATH` 를 «덮어쓴다». 둘 다 도구가
site-packages 에 있는 rig 에서 직접 확인했다: mypy 12시험 OK, import-linter
`Analyzed 262 files, 1332 dependencies · 3 kept, 0 broken`.

## 재현

```
# rig A (게이트와 동일)
MYPYPATH="." mypy -m fcc_test_platform.application.central_<name>_read_adapter

# rig B (커널 타입 해소)
MYPYPATH=".:../fcc-test-contracts:../fcc-test-contracts/packages/fcc-test-kernel" \
  mypy -m fcc_test_platform.application.central_<name>_read_adapter

# 봉인
python -m unittest tests.test_read_port_conformance -v
```
