# FCC 중앙 PC 운영 포트·기동 기준

> 🟡 **참조 문서 — 포트 기준.**
> 챔버 PC 절차는 [`chamber-pc-operational-verification-runbook.md`](https://github.com/junnv93/FCC_mobile_test_automation/blob/main/docs/operations/chamber-pc-operational-verification-runbook.md) §1 표를 보십시오.
> ⚠️ **포트 숫자의 권위는 문서가 아니라 코드입니다** — 챔버 노드는 `session_node_entry.py --print-config` 의 `node_port`, 중앙은 `infra/docker-compose.central.yml`. 문서와 다르면 코드가 옳습니다.

상태: 운영 전환 기준안

이 문서는 현재 중앙 PC에서 운영 중인 EMS와 FCC를 함께 운용하기 위한 FCC의
네트워크·기동 기준이다. EMS의 운영 방식은
`/home/devuser/equipment_management_system/docs/operations/central-pc-reboot-ops-guide.md`
를 참고한다.

## 1. 운영 주소

| 시스템 | 주소 | 비고 |
| --- | --- | --- |
| EMS | `http://10.206.34.233:8090` | 기존 운영 서비스. FCC와 포트가 겹치지 않는다. |
| FCC Platform | `http://10.206.34.233:8080` | 시험원이 브라우저에서 접속하는 주소 |
| FCC 로그인 | `http://10.206.34.233:8081` | Keycloak 로그인·토큰 발급 |

### 🔴 `:8080` 안에서 경로가 두 인스턴스로 갈립니다 (2026-09-04 이후)

포트는 하나지만 **뒤에 `platform-api` 인스턴스가 둘**입니다. 평문 HTTP 에서 브라우저와
챔버 노드가 요구하는 인증 모드가 반대라, nginx 가 경로로 가릅니다.

| 요청 경로 | 도달 인스턴스 | 인증 모드 |
|---|---|---|
| `/platform/*` (일반) | `platform-api` | `local_jwt` |
| `/platform/chambers/heartbeat` | `platform-api-node` | `oidc_jwt` |
| `/platform/chambers/{id}/reference-bundle` | `platform-api-node` | `oidc_jwt` |
| `/platform/chambers/{id}/result-ingestions` | `platform-api-node` | `oidc_jwt` |

⚠️ **챔버 PC 설정은 바뀌지 않았습니다** — 노드는 계속 `:8080` 만 봅니다. 분기는
중앙 nginx 안에서만 일어납니다.

⚠️ **임시 형상입니다.** 인증서가 발급되면 노드 인스턴스와 nginx 블록을 지우고 단일
`oidc_jwt` 로 되돌립니다.

`10.206.34.233`은 FCC 중앙 PC의 고정 LAN IP로 사용한다. FCC의
`PUBLIC_HOST`도 같은 IP를 사용해야 로그인 issuer와 브라우저 origin이 일치한다.

## 2. 보안 승인 기준 포트

| 받는 PC | 인바운드 TCP | 출처 | 용도 |
| --- | ---: | --- | --- |
| FCC 중앙 PC | `8080` | 시험원 PC, 챔버 PC | 웹 Gateway, `/platform/*` heartbeat·등록 |
| FCC 중앙 PC | `8081` | 시험원 PC, 챔버 PC | OIDC 로그인·머신 토큰 발급 |
| 각 챔버 PC | `9000` | FCC 중앙 PC만 | Session API: 측정 시작·진행률 |
| FCC 중앙 PC | `8090` | 해당 없음 | EMS가 사용 중이므로 FCC가 사용하지 않음 |

챔버 PC의 `9000` 규칙은 중앙 PC IP만 source로 제한한다. 중앙 PC의 `8090`은
EMS가 이미 사용하므로 FCC compose에 추가하거나 변경하지 않는다.

### ⚠ 중앙 PC `8080` 선점 — National Instruments 웹서버 (2026-08-06 실측)

중앙 PC(`SUW0521PC1WNBRE`)에서 **NI Application Web Server 가 `0.0.0.0:8080` 을
선점**하고 있어 FCC 게이트웨이가 LAN 에서 응답하지 못했다. 이 실패는 **조용하다**:

- `Test-NetConnection 10.206.34.233 -Port 8080` → `TcpTestSucceeded : True`
  (NI 가 응답하므로 성공으로 보인다)
- `curl http://10.206.34.233:8080/platform/chambers` → `404`,
  `Server: Embedthis-http` ← FCC nginx 가 아니다
- 같은 요청을 WSL 안에서 `localhost:8080` 으로 보내면 `200` ← 앱은 정상

즉 **포트 점검이 True 여도 우리 서비스가 아닐 수 있다.** `Server:` 헤더로 응답
주체를 확인해야 진짜 판정이다.

원인은 `netsh portproxy` 가 이미 점유된 소켓을 가져오지 못하기 때문이다. `8081` 은
`svchost`(IP Helper)가 정상적으로 잡고 있어 대조군이 된다.

**조치** (관리자 PowerShell):

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8080 | ForEach-Object { Get-Process -Id $_.OwningProcess | Select-Object Id,ProcessName,Path }
Get-Service -Name NIApplicationWebServer -DependentServices    # 비어 있어야 안전
Stop-Service -Name NIApplicationWebServer -Force
Set-Service  -Name NIApplicationWebServer -StartupType Manual  # 재부팅 재발 방지
netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=8080
netsh interface portproxy add    v4tov4 listenaddress=0.0.0.0 listenport=8080 connectaddress=<WSL_IP> connectport=8080
```

`NIApplicationWebServer` 는 의존 서비스가 없다(부모는 `NISystemWebServer` 이며 그쪽은
건드리지 않는다). VISA/DAQ 드라이버와 별개 서비스라 계측기 통신에 영향이 없다.
`StartupType` 을 `Manual` 로 두지 않으면 **재부팅 때 8080 을 다시 뺏고, 증상이 같아서
같은 진단을 처음부터 반복하게 된다.**

### ⚠ 평문 HTTP 운영 — 의도적 사양 이탈 (2026-08-06, 운영검증 기간)

중앙 스택은 사내망에서 **평문 HTTP** 로 서비스된다(`8080`/`8081`). 사내 CA 인증서를
받을 수 없어 TLS 를 붙이지 못한 상태이며, 같은 PC 의 EMS 도 동일하게 http 로 운영 중이다
(`infra/compose/lan.override.yml` 의 `NEXTAUTH_URL: http://...`).

**이것은 사양 위반이며 그렇게 기록한다:**

- RFC 6749 §3.1/§3.2 — 인가·토큰 엔드포인트에 대해 *"MUST require the use of TLS"*
- RFC 9700 — 인가 응답은 *"MUST NOT be transmitted over unencrypted network connections"*

**실질 위험**: OIDC access token 이 평문으로 흐른다. 같은 네트워크 경로에 접근 가능한
사람은 토큰을 읽고 재사용할 수 있다. 현재는 사내망 접속자로 접근이 제한된 **운영검증
기간**의 수용된 위험이다.

**어떻게 켜는가** — 기본값은 안전하고, 이탈은 배포가 명시적으로 선언해야 한다:

```bash
# infra/central/central.env
ALLOW_INSECURE_TRANSPORT=true      # 기본 false
```

이 값이 `runtime-config.js` 의 `insecureTransportAllowed` 로 주입되고, SPA 의 Zod 검증이
그때만 http 를 허용한다. 선언하지 않으면 SPA 는 **부팅을 거부**한다 — 이는 결함이 아니라
설계된 fail-fast 이며, TLS 도입 후 오타난 엔드포인트를 잡아주는 안전망이다.

**상환 조건**: 사내 인증서를 확보하는 즉시 `central.env` 에서 이 줄을 지우고
`PUBLIC_HOST` 기반 URL 을 https 로 전환한다. Keycloak realm origin(`central_realm_add_origin.py`)과
챔버 노드의 `FCC_CENTRAL_BASE_URL` 도 함께 바꿔야 한다.

### WSL NAT + portproxy 의존성

이 중앙 PC 의 WSL 은 `networkingMode` 미설정 = **NAT 모드**다. 따라서 컨테이너
published 포트는 WSL 내부에만 열리고, LAN 노출은 `netsh portproxy` 가 담당한다
(EMS `8090` 이 쓰는 것과 같은 방식이며, FCC 도 여기에 맞춘다).

portproxy 의 `connectaddress` 는 **WSL 의 NAT IP 를 하드코딩**한다. 이 IP 는 재부팅 시
바뀔 수 있고, 바뀌면 EMS·FCC 가 **서비스는 정상인 채로 LAN 에서만 죽는다.** 재부팅
점검 항목에 반드시 포함한다:

```bash
hostname -I | awk '{print $1}'                                          # 현재 WSL IP
powershell.exe -NoProfile -Command "netsh interface portproxy show all" # 등록된 대상 IP
```

둘이 다르면 portproxy 를 지우고 다시 등록한다.

## 3. 중앙 PC 내부 포트와 LAN 포트의 구분

중앙 Docker 네트워크 내부에서는 다음 포트를 계속 사용한다.

| 내부 서비스 | 내부 포트 | LAN 운영자가 직접 접속하는가 |
| --- | ---: | --- |
| headless-api | `8001` | 아니오. `8080/headless/*` 뒤에 있음 |
| platform-api | `8002` | 아니오. `8080/platform/*` 뒤에 있음 |
| PostgreSQL | `5432` | 아니오. 중앙 DB 내부용 |
| Keycloak 컨테이너 HTTP | `8080` | 아니오. 호스트에서는 `8081` |

> 현재 `docker-compose.central.yml`은 개발·검증 편의를 위해 `8001`, `8002`, `5432`의
> 호스트 publish 설정도 가지고 있다. 따라서 위 표는 **보안 목표 상태**이고, 실제 운영에서
> 해당 포트를 LAN에 노출하지 않으려면 compose의 host binding 또는 중앙 PC 방화벽을
> 별도로 잠그는 작업이 남아 있다. 승인된 포트만으로 운영 전환하기 전 이 상태를 확인한다.

현재 compose는 `8002`를 호스트에 publish한다. **측정 결과 동기화는 더 이상 중앙 `5432`에
직결하지 않는다** — 챔버는 인증된 Platform API 경계
`POST http://10.206.34.233:8080/platform/chambers/{chamber_id}/result-ingestions`
로만 결과를 올리고, 중앙이 그 뒤에서 DB에 적재한다. 챔버 런타임은
`FCC_CENTRAL_DB_URL`도 psycopg도 사용하지 않는다. heartbeat sender 역시
`FCC_CENTRAL_BASE_URL`로 `8080` Gateway를 가리킨다.

따라서 승인된 `8080/8081/9000`만으로 운영을 선언하기 위해 남은 것은 **실측 확인**이다.

1. heartbeat·자가 등록이 `http://10.206.34.233:8080/platform/...` Gateway 경유로
   동작하는지 실챔버에서 확인한다.
2. 챔버 PC에서 `Get-NetTCPConnection -RemotePort 5432`가 **0건**인지 확인한다.
   (경로는 소스에서 닫혔고, 이 명령은 그 사실을 현장에서 재확인하는 절차다.)

이 두 항목이 실측되기 전에는 측정 자체와 중앙 결과 반영을 동일한 “운영 완료”로
표시하지 않는다.

## 4. 챔버 등록값

중앙 DB의 챔버 `base_url`과 챔버 PC 환경변수는 다음을 사용한다.

```text
FCC_CENTRAL_BASE_URL=http://10.206.34.233:8080
FCC_CENTRAL_NODE_BASE_URL=http://<NODE_IP>:9000
```

중앙에서 챔버를 등록할 때도 다음 형식이어야 한다.

```json
{
  "chamber_id": "chamber-a",
  "name": "1번 챔버",
  "base_url": "http://<NODE_IP>:9000",
  "enabled": true
}
```

`main_entry.exe`만 실행해서는 `9000`이 열리지 않는다. 챔버 PC에는 별도의
Session API 프로세스가 `0.0.0.0:9000`에서 수신 대기해야 한다.

## 5. 매일 기동·확인 순서

### 중앙 PC

```bash
cd /path/to/fcc-test-platform    # ⚠️ 중앙 PC 는 FCC 저장소를 두지 않는다 (2026-09-03)
hostname -I
grep -E '^PUBLIC_HOST=|^WEB_PORT=|^KEYCLOAK_PORT=' infra/central/central.env

docker compose -f infra/docker-compose.central.yml \
  --env-file infra/central/central.env ps

curl -fsS http://10.206.34.233:8080/health
```

정상 기준은 `PUBLIC_HOST=10.206.34.233`, `WEB_PORT=8080`,
`KEYCLOAK_PORT=8081`, 중앙 서비스 healthy, `/health` 응답 성공이다.

브라우저에서 다음 주소로 접속한다.

```text
http://10.206.34.233:8080
```

### 챔버 PC

관리자 PowerShell에서 각 챔버에 한 번만 다음 인바운드 규칙을 만든다.

```powershell
New-NetFirewallRule `
  -DisplayName "FCC Chamber Session API 9000" `
  -Direction Inbound `
  -Protocol TCP `
  -LocalPort 9000 `
  -Action Allow `
  -RemoteAddress 10.206.34.233 `
  -Profile Domain,Private
```

챔버 프로세스를 수동으로 실행한 뒤 확인한다.

```powershell
Get-NetTCPConnection -LocalPort 9000 -State Listen
Test-NetConnection 10.206.34.233 -Port 8080
Test-NetConnection 10.206.34.233 -Port 8081
```

중앙 PC에서는 각 챔버 IP를 향해 다음을 확인한다.

```powershell
Test-NetConnection <NODE_IP> -Port 9000
```

`9000`에 LISTENING 프로세스가 없으면 방화벽 규칙만 추가해도 원격 측정은
동작하지 않는다. Windows 서비스 자동시작은 보안 승인 전까지 사용하지 않고,
시험 시작 전에 운영자가 수동으로 Session API와 FCC 측정 프로그램을 실행한다.

## 6. 현재 정리 상태

| 항목 | 상태 |
| --- | --- |
| FCC 브라우저 주소 `10.206.34.233:8080` | 목표 운영 주소 확정 |
| FCC 인증 `10.206.34.233:8081` | 중앙 compose와 일치 |
| EMS `10.206.34.233:8090` | 기존 서비스로 유지, FCC와 분리 |
| 챔버 Session API `:9000` | 등록값·방화벽 기준 확정. 전용 `fcc-session-node.exe` + 런처 패키지가 존재하며 Windows 스모크 완료 |
| heartbeat를 중앙 `8080` Gateway로 전환 | 설정 SSOT 확정. 실챔버 실측 남음 |
| 결과 동기화 `5432` 제거 | **소스에서 완료** — 인증된 `/platform/chambers/{id}/result-ingestions` HTTP 경계로 대체. 챔버 측 `RemotePort 5432` 0건 실측 남음 |

> 마지막 두 행의 "실측 남음"은 코드가 미완성이라는 뜻이 아니라, 실장비·실챔버에서
> 아직 확인하지 않았다는 뜻이다. 두 구분을 섞지 않는다.

관련 문서:

- [챔버 Session Node 운영 런북](https://github.com/junnv93/FCC_mobile_test_automation/blob/main/docs/operations/chamber-session-node-operations.md)
- [중앙 PC 일상 검증](./central-pc-fcc-platform-verification-guide.md)
- [중앙 PC 최초 전환 런북](./central-pc-operational-validation-runbook.md)
- [챔버 토큰·RBAC 런북](https://github.com/junnv93/FCC_mobile_test_automation/blob/main/docs/operations/chamber-token-rbac-runbook.md)
- [시험원 핸드북](https://github.com/junnv93/FCC_mobile_test_automation/blob/main/docs/education/fcc-operator-handbook.html)
