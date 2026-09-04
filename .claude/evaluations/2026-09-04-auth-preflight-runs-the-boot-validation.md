# 사전점검이 부팅 검증을 돌리지 않았다 (2026-09-04)

## Why

중앙 PC 최초 구축에서 `check_auth_mode_pairing.py` 가 **`OK`** 를 냈다. 운영자는 그 뒤에
`FCC_*_LOCAL_JWT_ISSUER` 가 platform·headless 양쪽 다 없는 것을 **따로** 발견했다. 그대로
재기동했으면 `ValueError: local_jwt auth requires local_jwt_issuer` 로 부팅 거부였다 —
**도는 배포가 안 뜨는 배포로 바뀐다.** 사전점검의 존재 이유가 정확히 그것을 막는 것이다.

## What — 상태가 셋인데 축이 둘이었다

| 상태 | 누가 보나 |
|---|---|
| 컨테이너에 도달하지 않는다 | 봉인 `test_auth_mode_pairing.py::test_every_auth_field_reaches…` |
| **선언되지 않았다** | ⚠️ **아무도 안 봤다** |
| 선언됐고 값이 있다 | 정상 |

compose 는 `${FCC_PLATFORM_LOCAL_JWT_ISSUER:-}` 로 넘긴다 — **도달은 하므로** 봉인이 만족
된다. 그리고 `deployment_auth_defects` 의 짝 축은 platform 과 headless 를 **상등**으로만
보므로 둘 다 비면 「일치」로 통과한다. **두 축 모두 참인데 배포는 뜨지 않는다.**

## How

`deployment_auth_defects` 에 `local_jwt_configs` 를 더해 **부팅이 실제로 도는 검증**
(`LocalJwtConfig.validate`)을 재기동 전에 돌린다(계약 레인 `v0.1.15`).

⚠️ **필수 필드 목록을 재표현하지 않는다.** 그 목록은 `LocalJwtConfig` 가 소유한다. 여기서
다시 적으면 둘이 갈라지는 날 게이트가 「정합」이라고 말하면서 부팅은 거부된다 — **지금
닫는 결함과 같은 모양**이다. 그래서 import 하지 않고 호출자가 만든 설정을 받아 그 객체에게
묻고, 사유도 부팅이 내는 문장을 그대로 나른다.

## Verification

```
미선언          → exit 2 (판정 불가) + 빠진 여섯 키를 이름으로 댄다
선언·부팅 불가   → exit 1 + "local_jwt_secret of at least …" (부팅 오류 그대로)
정상            → exit 0 OK
oidc 모드       → 이 축이 안 걸린다 (기존 OIDC 결함만 발화)
lane_check      선언 0 / 관측 0 ✅
```

⚠️ **`exit 2` 가 옳다.** 이 파서는 `KEY=` 를 compose 와 같게 *미설정* 으로 읽으므로
(`read_env_text` 주석 3) 「선언했는데 빈 값」이라는 상태가 이 층에 없다. 요점은 **통과가
아니라는 것**이고, 미선언 축을 통과로 접지 않는 것이 이 스크립트의 규율이다.

## 후속 — 이 웨이브가 스스로 두 번 걸린 것

1. **첫 배선이 `None` 과 `''` 를 `or ''` 로 뭉갰다.** 이 스크립트 전체가 그 구분 위에
   서 있는데(미선언 2 · 어긋남 1), **규율을 고치는 커밋이 같은 규율을 깼다.**
   「PUBLIC_HOST 를 안 물었다」를 검증하던 검사가 2 대신 1 을 받아 드러났다.
2. **재현 검사가 한 갈래만 봤다.** 「미선언」 경로만 재면 값을 읽고 `validate` 를 부르는
   코드가 통째로 죽어 있어도 통과한다. 여섯 값을 전부 선언하되 시크릿을 최소 길이 미만으로
   두는 검사를 따로 세워, **검증이 실제로 도는 것**을 봉인했다.

## 🔴 남은 것 — `-29` 가 제기 (`2026-09-04-http-dual-auth-node-lane.md` §후속 3)

이중 인스턴스 배치에서는 `central.env` 하나가 두 표면을 설명하지 않는다. 브라우저용
`platform-api`(local_jwt)와 노드용 `platform-api-node`(oidc_jwt)가 나뉘었으므로, 이 도구가
`central.env` 만 보고 판정하면 **「노드가 401 일 것」이라고 잘못 경고**할 수 있다.
compose 에 `platform-api-node` 가 있는지를 축으로 더할지 미판정.
