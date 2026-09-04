# 중앙 PC 재부팅 후 FCC 운영 가이드

> 🔴 **필독 — 중앙 PC 재부팅 후/매일 기동.**
> 챔버 PC 기동은 [`chamber-pc-operational-verification-runbook.md`](https://github.com/junnv93/FCC_mobile_test_automation/blob/main/docs/operations/chamber-pc-operational-verification-runbook.md) **C4** 입니다.
> ⚠️ 최종 갱신 2026-08-06. 챔버 승인 축(`mode_verdict`) 확인이 이 문서에 없습니다 — 그 절차는 챔버 런북 **C5-3** 에 있습니다.

EMS의 [중앙 PC 재부팅 운영 가이드](../../../equipment_management_system/docs/operations/central-pc-reboot-ops-guide.md)와 같은 형식으로, 중앙 PC가 재부팅된 뒤 FCC를 수동으로 확인·기동하는 절차를 정리한 문서다.

대상 운영 주소:

```text
FCC Platform: http://10.206.34.233:8080
FCC Login:    http://10.206.34.233:8081
EMS:          http://10.206.34.233:8090  (기존 서비스, FCC와 무관)
```

모든 중앙 PC 명령은 WSL Ubuntu 터미널에서 실행한다.

## 1. 작업 위치와 IP 확인

```bash
cd /path/to/fcc-test-platform    # ⚠️ 중앙 PC 는 FCC 저장소를 두지 않는다 (2026-09-03)

# ⚠️ WSL 안에서 `hostname -I` 로 판정하지 마라 — 아래 §1.1 을 먼저 읽어라.
powershell.exe -NoProfile -Command "hostname; (Get-NetIPAddress -AddressFamily IPv4).IPAddress"
grep -E '^PUBLIC_HOST=|^WEB_PORT=|^KEYCLOAK_PORT=' infra/central/central.env
```

정상 기준:

- 위 PowerShell 출력의 호스트 이름이 `SUW0521PC1WNBRE` 이고 IPv4 목록에
  `10.206.34.233` 이 있다.
- `PUBLIC_HOST=10.206.34.233`
- `WEB_PORT=8080`
- `KEYCLOAK_PORT=8081`

IP가 다르면 로그인 origin과 토큰 issuer가 어긋날 수 있으므로 먼저 운영 담당자에게
확인한다.

### 1.1 ⚠️ `hostname -I` 는 이 기계에서 **원리적으로** 틀린 판정자다

이 절은 오래 *「`hostname -I` 에 `10.206.34.233` 이 포함된다」* 를 정상 기준으로 적었다.
**중앙 PC 의 WSL 은 NAT 모드라 그 문장은 참이 될 수 없다** — 실측 2026-09-04:
`172.25.63.61` 과 도커 브리지 대역만 나오고 LAN 주소는 나오지 않는다.

⚠️ **틀리는 방향이 나쁘다.** 이 저장소가 이름 붙인 함정은 *「개발 PC 를 중앙으로 오인」*
이었는데(드리프트 게이트가 보고 맨 위에 측정 기계를 적는 이유), 이 문장은 반대로
**「중앙을 중앙이 아니라고 오판」** 하게 만든다. 2026-09-04 에 운영자와 세션이 실제로
그 직전까지 갔다.

뿌리는 두 기계의 **WSL 네트워킹 모드가 다르다**는 것이다:

| 기계 | WSL 모드 | `hostname -I` 가 보여주는 것 |
|---|---|---|
| 개발 PC | mirrored | LAN IP — 그래서 이 문장이 **거기서는 참이었다** |
| 중앙 PC (`SUW0521PC1WNBRE`) | NAT | `172.25.x` + 도커 브리지뿐 |

즉 이 기준은 **개발 PC 에서 쓰여 중앙 PC 문서에 실린 것**이다. 같은 명령이 두 기계에서
다른 것을 답하는데 문서는 한쪽만 봤다.

**판정은 Windows 호스트에게 물어라.** WSL 의 인터페이스는 그 기계의 LAN 소속을 답하는
축이 아니다 — `ps` 의 argv 가 프로세스의 `cwd` 소속을 답하는 축이 아닌 것과 같은 형태다
(`.claude/rules/check-axis-blindness.md` §증거 4). `central.env`의 비밀번호·시크릿은 화면이나 로그에 출력하지 않는다.

## 2. Docker 상태 확인 및 기동

```bash
docker ps

docker compose -f infra/docker-compose.central.yml \
  --env-file infra/central/central.env ps
```

서비스가 내려가 있으면 수동으로 기동한다.

```bash
docker compose -f infra/docker-compose.central.yml \
  --env-file infra/central/central.env up -d
```

다시 상태를 확인한다.

```bash
docker compose -f infra/docker-compose.central.yml \
  --env-file infra/central/central.env ps
```

정상 기준은 `postgres`, `keycloak`, `headless-api`, `platform-api`, `web`이 실행 중이고,
`migrate`는 성공 후 `Exited (0)`인 상태다. 운영 데이터가 있는 중앙 PC에서는
`docker compose down -v`를 사용하지 않는다.

## 2-1. 재부팅 후 반드시 볼 것 — 8080 선점과 WSL IP (2026-08-06 추가)

WSL 이 NAT 모드라 LAN 노출은 `netsh portproxy` 가 담당한다. 재부팅은 그 전제를 두
가지 방식으로 깨뜨리며, **둘 다 서비스가 정상인 채로 LAN 에서만 죽는다** — 그래서
증상만 보면 원인을 찾기 어렵다.

**(a) NI 웹서버가 8080 을 다시 뺏었는가**

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8080 | ForEach-Object { Get-Process -Id $_.OwningProcess | Select-Object Id,ProcessName }
```

`ApplicationWebServer` 가 보이면 NI 가 선점한 것이다. `curl.exe -i http://10.206.34.233:8080/health`
의 `Server:` 헤더가 `Embedthis-http` 면 확정이며, `nginx` 여야 정상이다.
**`TcpTestSucceeded : True` 는 판정 근거가 못 된다** — NI 도 응답하기 때문이다.

```powershell
Stop-Service -Name NIApplicationWebServer -Force
Set-Service  -Name NIApplicationWebServer -StartupType Manual
```

**(b) WSL IP 가 바뀌었는가**

```bash
hostname -I | awk '{print $1}'
powershell.exe -NoProfile -Command "netsh interface portproxy show all"
```

`connectaddress` 와 현재 WSL IP 가 다르면 8080/8081(및 EMS 8090)을 지우고 다시 등록한다.

```powershell
netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=8080
netsh interface portproxy add    v4tov4 listenaddress=0.0.0.0 listenport=8080 connectaddress=<현재_WSL_IP> connectport=8080
```

상세 배경은 [포트·토폴로지 기준](./fcc-central-pc-port-topology.md) 참조.

## 3. 중앙 서비스 스모크 테스트

```bash
curl -fsS http://10.206.34.233:8080/health
curl -fsS http://10.206.34.233:8081/realms/fcc-dev/.well-known/openid-configuration \
  >/dev/null
```

두 명령이 성공하면 브라우저에서 다음 주소로 접속한다.

```text
http://10.206.34.233:8080
```

정상 기준:

- 로그인 화면이 표시된다.
- 로그인 후 FCC 홈 화면이 표시된다.
- `시험 챔버` 메뉴에서 등록 챔버가 보인다.
- 챔버가 실행 중이면 온라인 상태와 heartbeat 시각이 갱신된다.

## 4. 장애 시 로그

```bash
docker compose -f infra/docker-compose.central.yml \
  --env-file infra/central/central.env logs --tail=200 web

docker compose -f infra/docker-compose.central.yml \
  --env-file infra/central/central.env logs --tail=200 keycloak

docker compose -f infra/docker-compose.central.yml \
  --env-file infra/central/central.env logs --tail=200 platform-api
```

| 증상 | 먼저 볼 것 |
| --- | --- |
| 브라우저가 열리지 않음 | `web` 상태·로그, 중앙 Windows 방화벽 `8080`, **그리고 8080 을 누가 듣고 있는지** (아래 2-1) |
| 로그인 후 튕김 | `PUBLIC_HOST=10.206.34.233`, Keycloak `8081`, realm origin |
| 챔버가 오프라인 | 챔버 PC의 `FCC_CENTRAL_BASE_URL=...:8080`, 중앙 `8080` 도달성, 토큰 |
| 원격 측정 시작 실패 | 중앙에서 챔버 `<NODE_IP>:9000` 도달성, 챔버 Session API LISTENING 여부 |
| 측정은 됐지만 결과가 없음 | 챔버 outbox → 중앙 `8080/platform/chambers/{id}/result-ingestions` 경로를 본다. 챔버 토큰과 chamber_id 불일치는 403, 중앙 장애는 5xx로 나타나고 두 경우 모두 outbox가 결과를 보존한 채 재시도한다 |

## 5. 챔버 PC 기동 순서

Windows 서비스 자동시작은 보안 승인 전까지 사용하지 않는다. 시험원 또는 운영자가
챔버 PC에서 다음 순서로 수동 실행한다.

1. 장비 연결 상태를 확인한다.
2. Session API를 `0.0.0.0:9000`에서 실행한다.
3. FCC 측정 GUI를 실행한다.
4. `Get-NetTCPConnection -LocalPort 9000 -State Listen`으로 수신 대기를 확인한다.
5. 중앙 PC의 `http://10.206.34.233:8080`과 `:8081`에 도달하는지 확인한다.
6. 중앙 화면의 `시험 챔버`에서 온라인 상태를 확인한다.

현재 GUI 실행 파일만으로 Session API가 자동 실행된다고 가정하지 않는다. `main_entry.exe`
는 GUI 전용이며 `:9000`을 열지 않는다.

Session API 실행 파일/런처 패키징은 **완료됐다** — `fcc-session-node.exe` +
`run-session-node.ps1` + 배포 정책 JSON 이 하나의 operator package 로 배포된다. 설치·기동
절차는 [챔버 Session Node 운영 런북](https://github.com/junnv93/FCC_mobile_test_automation/blob/main/docs/operations/chamber-session-node-operations.md) 소관이다.

다만 **원격 측정 운영을 PASS 로 판정하는 기준은 여전히 실장비 측정 1건**이다. 패키징
완료는 그 전제조건이지 인수 자체가 아니다.

## 6. 관련 문서

- [FCC 중앙 포트·토폴로지 기준](./fcc-central-pc-port-topology.md)
- [중앙 PC 일상 플랫폼 검증](./central-pc-fcc-platform-verification-guide.md)
- [중앙 PC 최초 전환 런북](./central-pc-operational-validation-runbook.md)
- [챔버 토큰·RBAC 런북](https://github.com/junnv93/FCC_mobile_test_automation/blob/main/docs/operations/chamber-token-rbac-runbook.md)
