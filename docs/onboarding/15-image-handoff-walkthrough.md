# 이미지를 만들어 중앙에 넣기 — 명령 단위 실습

> **이 문서를 읽기 전에 `01-concepts-for-non-developers.md` §5 를 읽으십시오.**
> 여기서는 개념을 다시 설명하지 않고 **무엇을 어떤 순서로 치는지**만 씁니다.
>
> 이 절차는 **provider 개발자가 새 코드를 중앙에 반영할 때마다** 하게 됩니다.
> 한 번 하고 끝나는 설치가 아닙니다.

---

## 0. 전체 그림 — 다섯 구간

```
 ①빌드        ②확인        ③뽑기        ④옮기기       ⑤넣고 띄우기
 개발 PC      개발 PC      개발 PC      사람          중앙 PC
 ────────     ────────     ────────     ────────      ──────────────
 docker       docker       docker       USB ·         docker load
 build        inspect      save         공유폴더 ·     docker compose
                                        scp           up -d --force-recreate
                                                      docker inspect  ← ⑥ 다시 확인
```

⚠️ **⑥ 이 이 절차에서 가장 자주 빠지는 단계입니다.** 그리고 빠져도
**아무것도 실패하지 않습니다** — 그래서 빠집니다.

---

## 1. 빌드 — 개발 PC

```bash
cd <당신의 provider 저장소>

docker build \
  -f infra/central/Dockerfile.headless \
  --build-arg GIT_REVISION="$(git rev-parse HEAD)" \
  -t <당신-이미지-이름>:latest \
  .
```

### 각 조각의 뜻

| 조각 | 뜻 |
|---|---|
| `-f <경로>` | 어떤 Dockerfile 을 쓸지. 안 주면 현재 폴더의 `Dockerfile` |
| `--build-arg GIT_REVISION=…` | ⚠️ **필수.** 어느 커밋으로 만들었는지를 이미지에 새긴다 (§2) |
| `$(git rev-parse HEAD)` | 「지금 체크아웃된 커밋의 식별자」를 셸이 대신 채운다 |
| `-t <이름>:latest` | 만들어진 이미지에 붙일 이름표 |
| 마지막 `.` | **빌드 컨텍스트** — 도커에게 넘길 폴더. `COPY` 는 여기서만 복사할 수 있다 |

### ⚠️ 빌드 전에 확인할 것 셋

**① 커밋하지 않은 변경이 있으면 라벨이 «거짓말»을 합니다.**

`git rev-parse HEAD` 는 **마지막 커밋**을 답합니다. 아직 커밋 안 한 수정이 있으면
그 수정은 이미지에 들어가지만 **라벨은 커밋된 상태를 가리킵니다.**

```bash
git status --porcelain     # 아무것도 안 나와야 한다
```

**② `COPY` 하는 경로가 실제로 있는지.**

파일이 이사했는데 `COPY` 줄을 안 고치면 빌드가 죽습니다:

```
failed to compute cache key … "/logger_config.py": not found
```

⚠️ **이 실패는 파이썬 검사가 «잡지 못합니다».** 파이썬 축에서는 파일이 이사한 것이
정상이고, **빌드 축에서만** 결함이기 때문입니다. 실제로 이 저장소에서 그 일이 있었고,
그래서 `tests/test_dockerfile_copy_paths.py` 라는 별도 검사가 생겼습니다.

**③ 두 레인 의존은 «나눠서» 설치해야 합니다.**

```
fcc-test-platform 0.1.8  → fcc-test-contracts 0.1.11 을 요구
당신의 requirements 는   → fcc-test-contracts 0.1.26 을 선언
한 번에 설치하면          → ResolutionImpossible — 빌드가 아예 안 된다
```

중앙 `Dockerfile.api` 가 쓰는 방법 — **목록에서 «파생»해서 둘로 가릅니다**:

```dockerfile
RUN grep -v '@[[:space:]]*git+' requirements.txt > /tmp/third-party.txt \
 && grep    '@[[:space:]]*git+' requirements.txt > /tmp/lanes.txt \
 && pip install -r /tmp/third-party.txt \
 && pip install --no-deps -r /tmp/lanes.txt
```

⚠️ **핀 값을 Dockerfile 에 다시 적지 마십시오.** 적으면 SSOT 가 둘이 되고,
그 둘은 반드시 갈라집니다. 저장소 주석이 그 규칙을 명시합니다 —
*"the split is DERIVED from the file — the pins are never restated here."*

⚠️ **`python:*-slim` 에는 `git` 이 없습니다.** `git+https://` 의존을 설치하려면
pip 가 git 을 실행해야 하므로, 설치 전에 깔고 설치 후에 지웁니다.

---

## 2. 확인 — 만들어진 것이 «내가 의도한 것»인가

```bash
docker images <당신-이미지-이름>
docker inspect <당신-이미지-이름>:latest \
  --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
```

두 번째 명령의 출력을 **`git rev-parse HEAD` 와 눈으로 대조**하십시오.

| 출력 | 뜻 |
|---|---|
| `a1b2c3d…` (커밋과 일치) | ✅ 의도한 코드로 만들어졌다 |
| `a1b2c3d…` (커밋과 **다름**) | 🔴 다시 빌드하지 않았다 — 캐시된 옛 이미지다 |
| **빈 출력** | 🔴 `--build-arg` 를 빠뜨렸다. **판정 불가**이지 통과가 아니다 |

⚠️ **빈 값을 「괜찮겠지」로 넘기지 마십시오.** 저장소 주석 —
*"「unknown」 같은 기본값을 넣는 것은 같은 침묵을 예쁘게 쓴 것이고,
그럴듯한 값을 넣는 것은 «거짓말»이다."*

### 왜 이것이 필요한가 — 실제로 일어난 일 (2026-09-04)

```
개발 PC 의 :latest   → 하루 전 코드
그 안의 라이브러리    → 낡음
컨테이너 상태         → healthy
```

**낡은 코드와 낡은 라이브러리가 서로 정합해서 아무것도 실패하지 않았습니다.**
`:latest` 라는 이름표는 「다시 만들었는가」에 답하지 못합니다.

### 자매 함정 — 버전 번호도 답하지 못할 수 있습니다

`fcc-test-platform 0.1.8` 이 **커널 이관 전과 후 양쪽**에 붙어 있었습니다.
**버전이 안 올랐으므로 버전으로 판정할 수 없습니다.**
설치된 패키지의 **내용**을 보십시오:

```bash
docker run --rm <이미지> pip show -f fcc-test-platform | head -20
```

---

## 3. 뽑기 — 이미지를 파일 하나로

```bash
docker save <당신-이미지-이름>:latest -o headless-api.tar
ls -lh headless-api.tar          # 크기 확인. 보통 수백 MB ~ 수 GB
```

### `.tar` 가 무엇인지

**여러 파일을 하나로 묶은 파일**입니다. 압축은 기본적으로 안 됩니다
(원하면 `gzip headless-api.tar` 로 따로 압축할 수 있고, 그러면 `.tar.gz` 가 됩니다).

⚠️ **압축 프로그램으로 «풀지» 마십시오.** 안에 든 것은 폴더가 아니라
이미지의 층(layer)과 메타데이터이고, 손으로 풀면 도커가 그것을 이미지로 인식하지
못합니다. **반드시 `docker load` 로 풉니다.**

### 옮기기 전에 무결성 값을 남기십시오

```bash
sha256sum headless-api.tar > headless-api.tar.sha256
```

USB 로 옮기다 파일이 깨질 수 있고, **깨진 파일은 `docker load` 가 이상한 오류로
실패**합니다. 중앙 PC 에서 같은 명령을 돌려 값이 같은지 보면
**「옮기다 깨졌나」와 「원래 잘못 만들었나」를 가를 수 있습니다.**

---

## 4. 옮기기 — 사람이 하는 구간

```
USB · 공유 폴더 · scp
```

⚠️ **폐쇄망이라 이 구간이 자동화되지 않습니다.** 그래서 여기서
「어느 파일이 최신인지」가 사람의 기억에 의존하게 되고,
**그것이 §2 의 리비전 라벨이 존재하는 진짜 이유**입니다.

**파일 이름에 날짜와 커밋 앞자리를 넣으면 그 기억이 필요 없어집니다:**

```bash
docker save <이미지>:latest -o "headless-api-$(date +%Y%m%d)-$(git rev-parse --short HEAD).tar"
# → headless-api-20260907-a1b2c3d.tar
```

---

## 5. 넣고 띄우기 — 중앙 PC

```bash
# ① 무결성 확인 (옮기다 깨졌는지)
sha256sum -c headless-api.tar.sha256

# ② 이미지를 도커 안으로 되돌린다
docker load -i headless-api.tar

# ③ 들어왔는지 본다
docker images <당신-이미지-이름>

# ④ 컨테이너를 «재생성» 한다
cd <fcc-test-platform 위치>
docker compose -f infra/docker-compose.central.yml up -d --force-recreate headless-api
```

### ⚠️ ④ 의 `--force-recreate` 가 핵심입니다

`docker compose up -d` 만 치면 — **태그가 같으므로** 도커는
「이미 그 이미지로 돌고 있다」고 판단하고 **아무것도 하지 않습니다.**
명령은 성공하고, 컨테이너는 **옛 코드로 계속 돕니다.**

**「명령이 성공했다」와 「새 코드가 돌고 있다」는 다른 명제입니다.**

---

## 6. ⭐ 다시 확인 — 이 단계를 빼지 마십시오

```bash
# 돌고 있는 컨테이너가 «어느 커밋» 인가
docker inspect fcc-central-headless-api \
  --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'

# 떠 있나
docker compose -f infra/docker-compose.central.yml ps

# 실제로 응답하나 — ⚠️ 127.0.0.1 이 아니라 «네트워크 주소» 로
curl http://<중앙 PC 의 IP>:8001/headless/metrics
```

### 판정표

| 확인 | 기대값 | 아니면 |
|---|---|---|
| 리비전 라벨 | 방금 빌드한 커밋 | ④ 를 안 했거나 옛 `.tar` 를 넣었다 |
| `ps` 상태 | `Up (healthy)` | 로그를 보라 — `docker compose logs headless-api` |
| `curl` 응답 | HTTP 200 | 포트가 막혔거나 컨테이너가 안 떴다 |

⚠️ **`curl` 을 반드시 네트워크 주소로 재십시오.** `127.0.0.1` 로만 재면
**「떠 있다」와 「밖에서 쓸 수 있다」를 구분하지 못합니다.**
챔버 PC 는 밖에서 접속하는 쪽입니다.

⚠️ **`docker ps` 출력은 여러 기계에서 «완전히 같은 모양»입니다.**
같은 이름의 컨테이너가 개발 PC 와 중앙 PC 양쪽에서 돌기 때문입니다.
2026-09-04 라운드에서 **세 세션이 각자 다른 기계를 재고 같은 이름으로 보고**했습니다.
**보고할 때 어느 기계에서 쟀는지 주소를 적으십시오.**

---

## 7. 되돌리기 — 새 이미지가 문제일 때

`docker load` 는 **옛 이미지를 지우지 않습니다.** 태그가 옮겨갈 뿐입니다.
그래서 되돌릴 수 있습니다 — **옛 이미지의 ID 를 알고 있다면.**

```bash
# 미리 (새 것을 넣기 «전»에) 옛 이미지에 이름을 하나 더 붙여 둔다
docker tag <이미지>:latest <이미지>:before-20260907

# 되돌릴 때
docker tag <이미지>:before-20260907 <이미지>:latest
docker compose -f infra/docker-compose.central.yml up -d --force-recreate headless-api
```

⚠️ **이 대비를 «넣기 전»에 해 두십시오.** 넣은 뒤에는 `:latest` 가 이미 옮겨갔고,
옛 이미지는 이름 없는(`<none>`) 상태로 남아 ID 로만 찾을 수 있습니다.

---

## 8. 순서가 왜 이런가 — 되돌리기 비용

```
① 계약 검사        당신 CI      아무것도 안 건드림          ← 공짜
② 이미지 빌드      당신 PC      로컬
③ 이미지 load      중앙        되돌릴 수 있음 (§7)
④ compose up       중앙        컨테이너만
⑤ 챔버 프로비저닝   챔버 PC     ⚠️ 파일·방화벽·ACL 변경     ← 비싸다
⑥ 노드 기동        챔버 PC
⑦ 연결 확인        양쪽
```

**공짜인 것을 앞에, 되돌리기 비싼 것을 뒤에 둡니다.**

⚠️ **⑤ 는 반드시 `-ValidateOnly` 를 먼저 돌리십시오.** 실측(2026-09-04):
그 단계가 **여섯 개의 결함을 아무것도 안 건드린 상태에서** 하나씩 드러냈습니다.
그냥 설치했다면 파일이 깔리고 방화벽이 열린 뒤에 실패해,
**반쯤 설치된 상태에서** 원인을 찾아야 했을 것입니다.

---

## 9. 체크리스트 — 인쇄해서 옆에 두십시오

```
개발 PC
  [ ] git status 가 깨끗한가            (커밋 안 한 변경이 있으면 라벨이 거짓말한다)
  [ ] docker build 에 --build-arg GIT_REVISION 을 넣었나
  [ ] docker inspect 로 라벨이 git rev-parse HEAD 와 «같은지» 봤나
  [ ] docker save 로 .tar 를 만들고 sha256 을 남겼나
  [ ] 파일 이름에 날짜와 커밋 앞자리가 들어갔나

중앙 PC
  [ ] sha256sum -c 로 옮기다 안 깨졌는지 확인했나
  [ ] (되돌리기 대비) 옛 이미지에 :before-<날짜> 태그를 붙였나
  [ ] docker load 했나
  [ ] up -d 에 --force-recreate 를 «넣었나»       ← 가장 자주 빠진다
  [ ] docker inspect 로 «돌고 있는» 컨테이너의 라벨을 다시 봤나   ← 가장 자주 빠진다
  [ ] curl 을 127.0.0.1 이 아니라 네트워크 주소로 쟀나

보고
  [ ] 어느 기계에서 쟀는지 «주소»를 적었나
```

---

## 10. 📋 Claude Code 에게 시킬 때

```
우리 headless API 이미지를 새로 빌드해서 중앙에 넣으려고 한다.
docs/onboarding/15-image-handoff-walkthrough.md 의 절차를 따라줘.

⚠️ 먼저 §1 의 「빌드 전에 확인할 것 셋」을 실제로 확인하고 결과를 보고해라.
   git status 가 깨끗하지 않으면 «멈추고» 물어봐라 — 라벨이 거짓말하게 된다.

⚠️ 각 단계마다 §2·§6 의 판정을 실제로 «실행해서» 보여줘라.
   「했다」가 아니라 «출력»을 보여 달라.

⚠️ 중앙 PC 에서 도는 명령(③④)은 실행하지 말고 «내가 칠 명령»으로 출력만 해라.
   그것은 내 승인이 필요한 구간이다.
```

⚠️ **마지막 줄이 중요합니다.** 중앙 PC 의 컨테이너를 재기동하는 것은
**되돌리기 비싼 작업**이고, 이 프로젝트에서 **사람의 승인이 따로 필요한 항목**으로
이름 붙여져 있습니다(`CLAUDE.md` §P0-5).
