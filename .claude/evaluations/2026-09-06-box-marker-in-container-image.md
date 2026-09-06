# 이미지가 상자 표식을 들고 있는데 세는 쪽이 그것을 안 셌다

측정 2026-09-06 · 기준 `main` = `84f2955` · 세션 fcc-delivery-final (a241f0f7)

## Why — 어디서 죽었나

개발 PC 의 중앙 스택을 `origin/main` 으로 갱신하다가 `central-migrate` 가 죽었다:

```
service "central-migrate" didn't complete successfully: exit 2
{"ok": false,
 "error": "마이그레이션 디렉터리가 없다: /docs/platform/migrations
           이것은 «적용할 것이 없다» 가 아니라 «찾지 못했다» 다."}
```

⚠️ **이 오류 문언이 이 결함을 살렸다.** 러너가 「0건 적용」으로 강등했다면 잡은
성공하고, 빈 스키마 위에서 뒤따르는 모든 실패가 원인과 무관해 보였을 것이다.
2026-09-03 에 그 강등을 막아 둔 것이 여기서 값을 냈다.

## What — 두 옳은 결정이 만나서 생긴 결함

**(1) Dockerfile 은 설치 뒤 `pyproject.toml` 을 «의도적으로» 지운다.**

```dockerfile
RUN pip install --no-deps . \
    && rm -rf /app/fcc_test_platform … /app/pyproject.toml /app/README.md …
```

소스를 남기면 `/app` 이 cwd 이므로 **휠보다 먼저 import 되어 휠을 조용히 가린다.**
이 이미지가 「휠만으로 돈다」를 주장하는데 실제로는 트리로 도는 상태가 된다.
그 삭제는 옳다.

**(2) `repository_anchor` 는 상자 표식으로 `pyproject.toml` «하나만» 인정했다.**

그 헬퍼는 오늘(2026-09-06) 다른 결함에서 나왔다 — 설치된 배포판에서 `__file__` 이
`site-packages` 라 조상에 `docs/` 가 없고, 걷기가 최외곽까지 올라가
`/docs/platform/…` 을 답하던 문제다. 그 수리는 「모듈이 사는 곳이 아니라 **다루는
곳**」이라는 옳은 축을 골랐다. 다만 «다루는 곳»을 알아보는 표식이 하나뿐이었다.

**둘이 만나면**: 이미지 안에는 `pyproject.toml` 이 없다 → 앵커가 `site-packages` 로
되돌아간다 → `/docs/platform/migrations`.

⚠️ **이미지는 표식을 가지고 있었다.** 같은 Dockerfile 이 바로 그 자리를 메우려고
`.extraction-layout.json` 을 싣고, 주석이 이유를 적는다: *"상자 표식을 함께 싣는다 …
그래서 부르는 쪽이 경로를 몰라도 된다."* 그리고 `_tree_root` 는 그 기록을 **가장
먼저** 찾는다. 빠진 것은 표식이 아니라 **표식을 세는 쪽의 목록**이었다.

## How — 수리

```python
BOX_MARKERS = ('pyproject.toml', LAYOUT_RECORD_NAME)
```

후보 디렉터리가 바깥 고리, 표식이 안쪽 고리다 — **가장 가까운 트리가 이긴다.**
뒤집으면 먼 조상의 `pyproject.toml` 이 가까운 상자를 이긴다.

이 변경은 **「틀렸던 자리만」 움직인다**: 모노레포와 이 레인 체크아웃은 루트에
`pyproject.toml` 이 있어 답이 그대로이고, 표식이 그것뿐인 이미지에서만 답이 바뀐다.

## Verification — 실측

* 이미지 배치를 그대로 흉내 낸 트리(표식 + `migrations/`, `pyproject.toml` **없음**)
  에서 실제 `.extraction-layout.json` 으로 해소했다:

      앵커       /tmp/…/app/.extraction-layout.json
      해소 경로  /tmp/…/app/migrations
      sql 개수   35   (031~035 전부 포함)

* 새 봉인 9건 통과. **주입으로 이빨 확인** — `BOX_MARKERS` 를 결함 판
  (`('pyproject.toml',)`)으로 되돌리자 **세 축이 동시에 빨개졌다**:
  구조 축(이미지에 표식이 남지 않는다) 하나, 행동 축(표식만 있는 트리를 못 찾는다 ·
  가까운 상자가 진다) 둘. 복원하니 9/9.
* 레인 전량: `lane_check` EXIT=0 (3,214 passed · 실패 0).
* **컨테이너에서 실제로 확인했다 — 개발 PC 의 중앙 스택에서.** 종료코드가 축을
  하나씩 통과하며 바뀐 것이 이 판정의 뼈대다:

      ① 수리 전        exit 2  "마이그레이션 디렉터리가 없다: /docs/platform/migrations"
      ② 수리 후        exit 3  drift — 001 «하나만». 그리고 pending 이 **정확히 031~035**
                               ← 러너가 마이그레이션을 «보게 됐다»는 직접 증거다
      ③ reconcile      {"ok": true, "reconciled": ["001_initial_central_db"]}
      ④ up -d --build  exit 0 · 전 컨테이너 healthy (platform-api 둘 다)
      ⑤ status         applied 35 · **pending: [] · drift: []**

  ⚠️ ②가 이 실측의 핵심이다. 「고쳤더니 초록이 됐다」가 아니라 **「실패 지점이
  예고된 다음 관문으로 옮겨 갔다」**이고, 그 관문은 런북 §4-a ①-c 가 이 웨이브에
  대해 미리 적어 둔 바로 그것이다(`001` 재렌더 드리프트). 우연히 통과한 것과
  구별되는 모양이다.

## 왜 로컬 게이트가 이것을 못 봤나

`tests/test_dockerfile_copy_paths.py` 가 이미 이름 붙인 축 맹점이다 — import 폐포 ·
설치된 배포판 · pytest 셋 다 *"이미지 안에서 무엇이 살아남는가"* 를 묻지 않는다.
체크아웃에는 `pyproject.toml` 이 있으므로 **모든 로컬 검사가 초록**이고, 결함은
`docker compose up` 에서만 나타난다.

`test_dockerfile_copy_paths.py` 는 「COPY 원본이 실재하는가」를 본다.
새 봉인 `tests/test_the_image_keeps_a_box_marker.py` 는 **그 다음 질문**을 본다:
「복사된 것 중 무엇이 `rm` 뒤에 살아남고, 그중에 상자 표식이 있는가.」
둘 다 **빌드를 돌리지 않고** 답한다 — 빌드를 게이트로 쓰면 느리고 네트워크를 타서
꺼진다.

⚠️ **정직한 한계.** `rm -rf` 를 정규식으로 읽으므로 셸 전개(변수·글롭·`find -delete`)
는 보지 못한다. 이 검사가 세는 삭제 집합은 **하한**이다.
`test_the_delete_list_is_still_literal` 이 그 전제가 깨지는 날을 잡는다.

## 후속

1. ✅ 재빌드·기동으로 실증했다(위 Verification ①~⑤). 개발 PC 의 중앙 스택은 이제
   035 까지 적용돼 있다.
2. ⚠️ **중앙 PC 도 같은 결함을 만난다.** 중앙은 아직 030 이므로, 갱신하는 순간
   같은 자리에서 죽는다. 이 커밋이 그 앞을 막는다 — 중앙 담당 세션은 이 커밋을
   포함한 `main` 으로 갱신해야 한다.
3. ⚠️ 같은 헬퍼를 쓰는 다른 모듈 넷(`export_central_db_ddl_cli` ·
   `db_migration_runner_cli` · `cross_session_result_selection_evidence_cli` ·
   `db_migration_evidence`)도 이미지 안에서 같은 이유로 틀린 답을 냈을 것이다.
   이 수리가 그 넷을 함께 고친다 — **다만 그 넷이 이미지 안에서 실제로 불리는지는
   확인하지 않았다.**
