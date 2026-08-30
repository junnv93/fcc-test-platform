# 로컬 개발 — 개발 PC localhost 에서 중앙 스택 띄우기

이 문서는 **개발 PC에서 코드 작업/검증**을 위해 중앙 스택을 **localhost**로 돌리는
방법입니다. 운영 상시 가동(중앙 PC, LAN IP)은 별도 — `ONPREM_DEPLOYMENT.md` 참조.

> 업계표준: **개발은 localhost, 운영만 서버(LAN IP)**. 같은 compose가 `PUBLIC_HOST`
> env 하나로 두 모드를 모두 커버한다 — 기본값 `127.0.0.1`이라 **운영값을 덮어쓰지
> 않으면 자동으로 localhost 개발 모드**(realm origin 추가·시크릿 교체 불필요).

## 두 트랙

| | 개발 (이 문서) | 운영 (ONPREM_DEPLOYMENT.md) |
|---|---|---|
| 어디서 | 개발 PC | 중앙 PC |
| PUBLIC_HOST | `127.0.0.1` (기본값 그대로) | 중앙 PC 고정 LAN IP |
| 쓰는 사람 | 개발자 본인 | 시험원 + 챔버 노드 |
| realm origin | 기본값 그대로 | LAN origin 추가 |
| 시크릿 | dev 데모값 OK | 실제 시크릿 교체 |

## Docker 런타임 선택 — Docker Desktop vs WSL2 네이티브

Docker Desktop은 WSL을 **대체**하는 게 아니라 **WSL2를 엔진으로 그 위에 얹는** GUI/
통합 레이어다. Windows 개발자에겐 Docker Desktop이 업계표준(Windows 터미널에서 바로
`docker`, 자동 시작, VS Code 통합)이지만, 두 가지를 반드시 고려한다:

1. **라이선스** — Docker Desktop은 종업원 250명 이상 또는 연매출 $10M 이상 기업의
   상업적 사용 시 유료 구독이 필요하다. 회사 자산 PC면 확인 필요. 무료 대안:
   **Rancher Desktop** / **Podman Desktop**(동일 UX, WSL2 백엔드).
2. **⚠ 이 repo의 충돌 이력(2026-06-22)** — 이 개발 PC는 과거 **Docker Desktop +
   WSL 네이티브 systemd `docker.service` 공존**이 dockerd 주기적 재시작 → 전 컨테이너
   `Exit 255`를 일으켜, **Docker Desktop을 제거하고 네이티브 docker 단독으로 정착**
   했다(`dev-preview-idp-robustness`). 한 머신에서 두 런타임을 동시에 두지 말 것.

| 선택 | 언제 | 주의 |
|---|---|---|
| **WSL2 네이티브 docker** | WSL 안에서 작업(VS Code↔WSL 연결), 서버/라이선스 무관 | `service docker start` 수동 → systemd 자동시작으로 해결(아래) |
| **Docker Desktop** | Windows에서 WSL 연결 없이 작업, 라이선스 OK | 설치 전 네이티브 `docker.service` 완전 비활성/제거(공존 충돌 방지) |

### 이 프로젝트의 권장 머신별 분리

**한 머신에 두 런타임을 공존시키지 않는다**(2026-06-22 공존 충돌 이력). 머신별로 하나만:

| 머신 | 런타임 | 작업 방식 |
|---|---|---|
| **중앙 PC** (운영 검증) | WSL2 네이티브 docker | VS Code ↔ WSL 연결, `ONPREM_DEPLOYMENT.md` |
| **개발 PC** (코드 작업) | Docker Desktop | Windows 터미널/VS Code에서 그대로, WSL 연결 없이 |

개발 PC를 Docker Desktop으로 갈 때는 WSL 네이티브 `docker.service`를 반드시 비활성화
(`sudo systemctl disable --now docker`)해 DD 단독으로 일원화한다.

### ⚠ 이미 다른 스택을 WSL 네이티브 docker 로 운영 중이면 — Docker Desktop 금지

이 개발 PC 처럼 **다른 docker 스택(예: EMS, equipment_management_system)을 WSL2
네이티브 docker 로 이미 운영 중**이라면 Docker Desktop 을 추가하지 않는다:

- **DD + 네이티브 공존** → dockerd 주기 재시작/전 컨테이너 `Exit 255`(2026-06-22 이력)
  → 기존 스택(EMS)까지 동반 중단.
- **DD 단독(네이티브 끄기)** → 네이티브로 돌던 EMS 운영이 즉시 중단.

대신 **같은 WSL 네이티브 docker 데몬에 포트만 분리해 공존**시킨다(데몬 하나, compose
프로젝트만 다름). 실증(이 PC, 동시 운영):

| 스택 | 호스트 publish 포트 |
|---|---|
| EMS | 5433(pg) · 6379(redis) · 9000-9001(rustfs) |
| FCC | 8080(web) · 8081(keycloak) · 8001(headless-api) · 8002(platform-api) · 15432(pg) |

포트가 겹치지 않으므로 충돌 없이 공존한다. FCC 웹 스택(headless-api 포함)은
win32-free 봉인이라(`tests/test_central_docker_compose.py`) WSL/Linux 에서 정상 동작 —
측정 runner(장비 GPIB/USB + Nuitka `.exe`)만 Windows 네이티브로 분리된다
(CLAUDE.md "챔버 노드는 컨테이너화 제외").

### A안 — WSL2 네이티브 docker 를 매끄럽게 (이 PC 권장, 충돌 이력)

"매번 `service docker start`" 불편을 systemd 자동시작으로 없앤다. WSL Ubuntu 안에서:

```bash
# /etc/wsl.conf 에 systemd 활성화
sudo tee /etc/wsl.conf >/dev/null <<'EOF'
[boot]
systemd=true
EOF
```
그 후 PowerShell 에서 `wsl --shutdown`, 다시 진입하면 systemd 가 docker 를 부팅 시
자동 시작한다(`sudo systemctl enable --now docker`로 부팅 등록 1회).
`docker compose version` 확인.

### B안 — Docker Desktop (깨끗한 PC + 라이선스 OK)

1. 설치 전 네이티브 docker 제거(공존 충돌 방지): WSL 에서
   `sudo systemctl disable --now docker` (또는 docker engine 패키지 제거).
2. Docker Desktop 설치 → Settings: **Use WSL 2 based engine** 켜짐 확인.
3. Settings → Resources → **WSL Integration** 에서 Ubuntu 토글 ON.
4. Settings → General → **Start Docker Desktop when you log in**.
5. Windows 터미널/VS Code 에서 `docker compose version` 확인.

## localhost 로 스택 띄우기

PUBLIC_HOST 기본값이 `localhost`라 env 의 운영값을 덮어쓰지 않으면 그대로 localhost:

```bash
cd /mnt/c/FCC_mobile_test_automation
cp infra/central/central.env.example infra/central/central.env   # 기본값 = localhost
docker compose -f infra/docker-compose.central.yml \
    --env-file infra/central/central.env up -d --build
```

- 브라우저: <http://localhost:8080>
- realm 은 이미 `localhost:8080`을 redirect/web origin 으로 허용 → **추가 작업 없음**.
- 시크릿 교체 불필요(dev 데모값으로 충분).
- 종료: `docker compose -f infra/docker-compose.central.yml down`
  (데이터까지 초기화: `down -v`)

## 더 가벼운 개발 루프 (compose 빌드 없이 HMR)

전체 central compose(정적 빌드)는 프론트 HMR 이 안 된다. 코드 개발은 vite dev 로:

- **풀스택 한 명령** (EMS `pnpm dev` 등가) — `cd apps/web && npm run dev:stack`:
  Keycloak(IdP) + 백엔드 3개(session/headless/platform, host venv) + vite 게이트웨이를
  한 번에. 상세/필수 env → `docs/development/local-dev-stack.md`.
- **프론트만 빠르게** — `scripts/preview-web.sh`(Keycloak + vite, `docs/development/dev-preview.md`).

## 네트워킹/성능 팁

- 이 PC 는 `.wslconfig` `networkingMode=mirrored`. 호스트→컨테이너 published 포트
  포워딩이 불안정할 수 있어, readiness 는 `docker compose up -d --wait`(컨테이너
  healthcheck 기반)로 판단한다 — Windows 측 `curl 127.0.0.1:PORT` 폴링에 의존하지 말 것
  (`dev-preview-idp-robustness` 의 자가파괴 루프 원인).
- repo 가 `C:\`(Windows FS)에 있어 WSL `/mnt/c/...` 접근 시 빌드 I/O 가 느리다. 다만
  데스크톱 GUI + 장비 제어가 Windows 에서 돌아야 해 repo 는 `C:\` 유지가 맞다(빌드 캐시
  이후엔 영향 적음).

## 관련 문서

- 운영 상시 가동: `infra/central/ONPREM_DEPLOYMENT.md`
- 중앙 스택 구성 SSOT: `infra/README.md` (중앙 허브 단일 스택 섹션)
