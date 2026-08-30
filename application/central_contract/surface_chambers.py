"""중앙 플랫폼 계약 — 챔버 등록 · 가용성 · heartbeat · 중앙 측정 프록시 · 설정.

``api_contracts`` facade 가 이 모듈의 표를 병합해 ``PLATFORM_API_*`` 로 재노출한다.
모듈 경계는 **표 종류가 아니라 operation 표면**이다 — git 이력 실측에서 계약 변경
커밋의 92%(52 중 48)가 정확히 한 표면만 만졌고, 표 종류로 자르면 엔드포인트 하나
추가가 항상 다섯 표를 동시에 만진다(네 표가 같은 operationId 키로 병렬 배열돼 있다).
"""
from __future__ import annotations

from application.central_contract.api_operation_factory import (
    _CHAMBER_NOT_FOUND_404,
    _operation,
)
from application.central_contract.api_vocabulary import (
    CENTRAL_SYNC_READINESS_CODE_VALUES,
    CHAMBER_RESULT_INGESTION_SCHEMA_VERSION,
    _CHAMBER_MODE_VERDICT_VALUES,
    _UNAVAILABLE_REASON_VALUES,
)

#: 이 모듈이 소유하는 route path prefix. 분할은 **선언이 아니라 판정 대상**이다 —
#: ``tests/test_central_contract_decomposition_axis.py`` 가 prefix 집합이 쌍마다
#: 서로소이고 ``PLATFORM_API_ROUTES`` 의 모든 경로를 덮는지, 그리고 각 operation 이
#: 자기 경로의 **최장 일치** prefix 를 소유한 모듈에 물리적으로 선언돼 있는지 파생
#: 판정한다. 새 엔드포인트가 어느 모듈로 가야 하는지 사람이 기억하지 않는다.
SURFACE_PREFIXES: tuple[str, ...] = (
    '/platform/chambers',
)


# 이 표면의 operation 만 참조하는 에러 응답 조각. 둘 이상의 표면이 참조하게 되면
# ``api_operation_factory`` 로 올라가야 하고, 그 판정도 파생 검사가 한다.
_CHAMBER_NOT_IDLE_409 = (
    'Chamber is not idle — it is in_use or offline (derived from heartbeat '
    'absence/staleness). A new measurement can only start on an idle chamber.'
)

_CHAMBER_FORWARD_503 = (
    'Chamber node Session API unreachable or returned an error — the central '
    'forward to {base_url}/session/... failed (ChamberProxyError).'
)


ROUTES: dict[str, tuple[str, str]] = {
    # 멀티챔버 P2 — chamber availability read + registry write share the
    # /platform/chambers path (one OpenAPI path item, GET + POST); heartbeat push
    # is a sibling POST. Chambers are global (no {project_id} segment).
    'list_chambers': ('GET', '/platform/chambers'),
    'register_chamber': ('POST', '/platform/chambers'),
    'push_chamber_heartbeat': ('POST', '/platform/chambers/heartbeat'),
    # Result transport boundary. The chamber sends a versioned envelope; the
    # central application owns parent-first mapping, SQL and idempotency.
    'push_chamber_result_ingestion': (
        'POST', '/platform/chambers/{chamber_id}/result-ingestions',
    ),
    # 멀티챔버 P5 — 중앙 측정 프록시. start 는 챔버별 측정 시작(중앙이 base_url 조회 후
    # 노드 /session/start 로 forward), progress 는 노드 /session/progress 폴링 forward.
    # 노드 Session API 는 single-session 모델(챔버 PC 당 동시 1 세션)이라 진행조회 경로에
    # session_id segment 를 두지 않는다 — 챔버 정체성이 곧 현재 실행이다(로드맵 .../progress).
    'start_chamber_measurement': (
        'POST', '/platform/chambers/{chamber_id}/measurements',
    ),
    'get_chamber_measurement_progress': (
        'GET', '/platform/chambers/{chamber_id}/measurements/progress',
    ),
    # 멀티챔버 P7/B4 — 중앙 진행 릴레이 WS fan-out 채널(ADR-0015 Option B). HTTP
    # method 가 아니라 'WEBSOCKET' sentinel — OpenAPI 빌더가 real path 에서 제외하고
    # x-fcc-websocket-paths + AsyncAPI 로 문서화한다(session subscribe_session_events
    # 동형). 라우트 문자열 SSOT — WS 핸들러/노드 client 가 이 상수에서 파생(하드코딩 금지).
    'subscribe_chamber_progress': ('WEBSOCKET', '/platform/chambers/events'),
    # 저장 위치는 챔버의 **속성**이므로 경로가 챔버 아래다. 쓰기와 읽기가 같은 자원을
    # 가리키되 권한 티어가 다르다(운영자 편집 / 노드 조회).
    'update_chamber_storage_root': (
        'PATCH', '/platform/chambers/{chamber_id}/storage-root',
    ),
    # 웹 세션 승인도 방의 속성이라 경로가 챔버 아래다. 다만 **행위자가 다르다** —
    # 저장 위치·계측기 주소는 시험원이 정하고, 승인은 회사 정책의 사실이라 운영자가
    # 정한다. 그래서 같은 자원 아래의 다른 경로이고 권한 티어도 다르다.
    'update_chamber_web_session_approval': (
        'PATCH', '/platform/chambers/{chamber_id}/web-session-approval',
    ),
    'get_chamber_settings': (
        'GET', '/platform/chambers/{chamber_id}/settings',
    ),
    # 계측기 연결 설정도 방의 속성이라 경로가 챔버 아래다. 저장 위치 축과 같은 형상
    # (읽기와 쓰기가 같은 자원을 가리키되 권한 티어가 다르다)이되, 여기서는 **읽기가
    # 둘**이다 — 운영자용(이 경로)과 노드용(위 settings). 노드가 별도 경로를 갖는
    # 이유는 그쪽만 경로 chamber_id 에 머신 토큰을 바인딩할 수 있기 때문이다.
    'get_chamber_equipment_config': (
        'GET', '/platform/chambers/{chamber_id}/equipment-config',
    ),
    'update_chamber_equipment_config': (
        'PATCH', '/platform/chambers/{chamber_id}/equipment-config',
    ),
}


PERMISSIONS: dict[str, str] = {
    # 멀티챔버 P2 — chamber registry / availability / heartbeat. Chambers are
    # GLOBAL infrastructure (not project-scoped): availability read shares
    # platform:read so any viewer can see which chamber is free, registration is
    # platform:admin (operator-provisioned), and heartbeat push uses a SEPARATE
    # node-scoped platform:chamber token — a chamber PC authenticates with a
    # machine token to self-report idle/in_use without being able to read
    # coverage/claims or register/manage other chambers. platform:chamber is
    # node-scoped, NOT a project-membership grant (it never appears in
    # rbac_role_grants — mirror of the headless 'public' exclusion).
    'list_chambers': 'platform:read',
    'register_chamber': 'platform:admin',
    'push_chamber_heartbeat': 'platform:chamber',
    # Chamber result transport is node-scoped exactly like heartbeat. The
    # server owns mapping/SQL and the token is bound to one chamber_id.
    'push_chamber_result_ingestion': 'platform:chamber',
    # 멀티챔버 P5 — 중앙 측정 프록시. 시작은 mutating 원격 액션이므로 engineer 티어
    # 토큰 platform:claim 으로 게이트(viewer 는 가용성/진행은 읽되 측정 시작 불가 =
    # 최소권한). 진행조회는 순수 read 라 platform:read. 새 platform:measure 토큰을
    # 도입하지 않는 이유: rbac_role_grants(docs/platform central schema)가 grant 집합을
    # 정확히 고정하고 (platform 권한 − node-scoped) == grant 동일성을 강제하므로, 새
    # grantable 토큰은 스코프 밖 central schema 변경을 요구한다. 기존 granted 토큰 재사용.
    'start_chamber_measurement': 'platform:claim',
    'get_chamber_measurement_progress': 'platform:read',
    # 멀티챔버 P7/B4 — 중앙 진행 릴레이 WS fan-out 구독(ADR-0015 Option B). 순수
    # read(웹이 챔버 진행을 실시간 구독)이므로 platform:read 공유 — 가용성/진행 폴링과
    # 동일 게이트. 별도 토큰 미도입(central rbac_role_grants 동일성 보존).
    'subscribe_chamber_progress': 'platform:read',
    # plot-changed-2026-08-11 — 챔버별 플롯 저장 위치.
    #
    # 2026-08-09 에는 이것이 platform:admin 이었다. 운영자 판정(2026-08-10, §6)이
    # 그 결정을 뒤집었다 — *"시험과 관련된 부분들은 당연히 시험원한테 권리가 모두
    # 있어야 해."* 저장 위치도 계측기 주소도 **방의 속성**이고 둘 다 시험 관련이다.
    #
    # 행위자(시험원)와 스코프(챔버)가 같은 토큰 둘은 **drift 표면일 뿐**이므로
    # (016 이 저작/게시 토큰 분리를 정확히 같은 논거로 기각했다) 두 축을 하나의
    # 챔버-속성 쓰기 토큰으로 합쳤다.
    #
    # 읽기는 여전히 **노드-스코프**다 — 노드는 자기가 어디에 써야 하는지만 알면 되고,
    # platform:read 를 주면 coverage / claims / memberships 까지 딸려가 "챔버 토큰은
    # 자기 것만 본다"는 최소권한 성질이 거짓이 된다.
    'update_chamber_storage_root': 'platform:chamber-config-write',
    # 챔버 모드 축 (2026-08-16) — **승인**은 회사 정책의 사실이고 운영자가 관리한다.
    # ⚠️ `platform:chamber-config-write` 재사용 **기각**: 그 토큰은 시험원이 갖고,
    # 시험원이 자기 PC 를 스스로 승인할 수 있으면 그 정책은 무력해진다. 2026-08-11 이
    # 저장 위치와 계측기 주소를 한 토큰으로 합친 근거는 *행위자와 스코프가 같다* 였고
    # 여기는 **행위자가 다르다**. 기존 `platform:admin` 재사용 → 신규 토큰 0.
    'update_chamber_web_session_approval': 'platform:admin',
    'get_chamber_settings': 'platform:chamber',
    # SPLIT-6 ② (2026-08-10) — 계측기 연결 설정. 저장 위치 축과 **의도적으로 다른
    # 티어**다. 운영자가 2026-08-10 에 "시험원이 직접 편집" 을 골랐고, 이유는 016 과
    # 같다: 재배선 뒤 분석기의 새 주소를 아는 사람은 관리자가 아니라 그 방에 서 있는
    # 시험원이다. platform:admin 을 유지하면 그 사람이 자기가 방금 읽은 주소를 기록할
    # 수 없어 워크북이 영영 권위를 놓지 못한다.
    #
    # platform:reference-write 재사용은 기각했다 — 그 토큰의 멤버십 경로는 PROJECT
    # 스코프 패밀리에서만 열리는데(그 판정 함수의 독스트링이 "방은 모든 프로젝트보다
    # 오래 존속하고 한 프로젝트가 두 방에 걸친다" 고 이미 적는다) 이 경로에는
    # project_id 가 아예 없다.
    #
    # 읽기는 **두 문**이다. 운영자 읽기는 platform:read(챔버는 프로젝트에 속하지 않는
    # GLOBAL 인프라라 list_chambers 와 같은 티어). 노드 읽기는 get_chamber_settings
    # 가 그대로 하고 응답에 additive 로 얹는다 — 그쪽 바인딩을 풀면 그 순간 노드 A 가
    # 노드 B 의 설정을 읽는다.
    'get_chamber_equipment_config': 'platform:read',
    'update_chamber_equipment_config': 'platform:chamber-config-write',
}


SCHEMAS: dict[str, dict] = {
    # 멀티챔버 P2 — chamber availability/registry/heartbeat envelopes. The
    # availability list carries server_time so the dashboard can show "as of"
    # freshness; each item exposes the verbatim heartbeat fields PLUS the
    # service-derived ``status`` (idle/in_use/offline — OFFLINE computed against the
    # read service's injected clock + per-chamber TTL, never by the DB view).
    'ChamberAvailabilityList': {
        'type': 'object',
        'required': ['items', 'server_time'],
        'properties': {
            'items': {
                'type': 'array',
                'items': {'$ref': '#/schemas/ChamberAvailabilityEnvelope'},
            },
            'server_time': {'type': 'string'},
        },
        'additionalProperties': False,
    },
    'ChamberAvailabilityEnvelope': {
        'type': 'object',
        'required': [
            'chamber_id', 'name', 'base_url', 'enabled',
            'heartbeat_ttl_seconds', 'status',
        ],
        'properties': {
            'chamber_id': {'type': 'string'},
            'name': {'type': 'string'},
            'base_url': {'type': 'string'},
            'enabled': {'type': 'boolean'},
            'heartbeat_ttl_seconds': {'type': 'integer'},
            'reported_status': {'type': 'string', 'nullable': True},
            'last_heartbeat_at': {'type': 'string', 'nullable': True},
            'session_id': {'type': 'string', 'nullable': True},
            # Derived availability — idle/in_use/offline.
            'status': {'type': 'string', 'enum': ['idle', 'in_use', 'offline']},
            # C1 heartbeat-carried progress — the latest heartbeat's progress
            # snapshot, present ONLY while the chamber is in_use (null otherwise).
            # A single availability read thus carries every chamber's live progress
            # (per-chamber progress-poll N+1 eliminated). nullable $ref idiom keeps
            # ChamberSessionProgress the single shape SSOT.
            'progress': {'nullable': True, 'allOf': [{'$ref': '#/schemas/ChamberSessionProgress'}]},
            # M2 diagnostics overlay. last_error / last_error_at are the node's
            # latest REDACTED operational error + when it was observed (null when
            # the node has not reported one). unavailable_reason explains WHY the
            # chamber is not usable — orthogonal to status (a disabled-but-
            # heartbeating chamber keeps status=idle with reason=disabled), which
            # is why it is NOT named offline_cause. null ⇒ usable. The enum
            # vocabulary is derived from the domain UnavailableReason SSOT.
            'last_error': {'type': 'string', 'nullable': True},
            'last_error_at': {'type': 'string', 'nullable': True},
            'unavailable_reason': {
                'type': 'string', 'nullable': True,
                'enum': _UNAVAILABLE_REASON_VALUES,
            },
            # 챔버 모드 축 (2026-08-16) — 승인(선언)과 대조 결과(파생)를 나란히 낸다.
            'accepts_web_sessions': {
                'type': 'boolean', 'nullable': True,
                'description': "Operator ruling on whether this chamber accepts web sessions (per-PC mode exclusivity, 2026-08-16). THREE-VALUED on purpose: null means nobody has ruled yet, which is a different operator action from an explicit false. This is the APPROVAL half only — whether the node actually opened a listener is observed separately from its heartbeat, and the mismatch between the two is the signal. Provider-neutral: what a chamber that does not accept web sessions runs instead is the provider's business and central does not model it.",
            },
            'mode_verdict': {
                'type': 'string',
                'enum': list(_CHAMBER_MODE_VERDICT_VALUES),
                'description': (
                    'Server-derived comparison of the approval ruling against the '
                    'observed listener. UNDECLARED = nobody has ruled (approval is '
                    'unknown, so nothing above it can be judged). POLICY_CONFLICT = '
                    'serving without approval — the one unambiguous signal on this '
                    'axis. NOT_OBSERVED = approved but no live node; it deliberately '
                    'does NOT claim a cause, because heartbeat absence cannot tell '
                    'apart not-running, a network fault, a missing env var, a central '
                    'outage, and nobody having logged in yet (a normal morning state). '
                    'CONSISTENT = nothing to do. Read this token; do not recompute it.'
                ),
            },
        },
        'additionalProperties': False,
    },
    'ChamberNodeEnvelope': {
        'type': 'object',
        'required': ['chamber_id', 'name', 'base_url', 'enabled', 'heartbeat_ttl_seconds'],
        'properties': {
            'chamber_id': {'type': 'string'},
            'name': {'type': 'string'},
            'base_url': {'type': 'string'},
            'enabled': {'type': 'boolean'},
            'heartbeat_ttl_seconds': {'type': 'integer'},
            'artifact_storage_root': {
                'type': 'string', 'nullable': True,
                'description': (
                    'Where this chamber PC writes measurement plots (plot-custody '
                    '②). Null means unset — the node then uses the workbook Save '
                    'Data "File Structure" cell.'
                ),
            },
            'accepts_web_sessions': {
                'type': 'boolean', 'nullable': True,
                'description': "Operator ruling on whether this chamber accepts web sessions (per-PC mode exclusivity, 2026-08-16). THREE-VALUED on purpose: null means nobody has ruled yet, which is a different operator action from an explicit false. This is the APPROVAL half only — whether the node actually opened a listener is observed separately from its heartbeat, and the mismatch between the two is the signal. Provider-neutral: what a chamber that does not accept web sessions runs instead is the provider's business and central does not model it.",
            },
        },
        'additionalProperties': False,
    },
    'RegisterChamberRequest': {
        'type': 'object',
        'required': ['chamber_id', 'name', 'base_url'],
        'properties': {
            'chamber_id': {'type': 'string', 'minLength': 1},
            'name': {'type': 'string', 'minLength': 1},
            'base_url': {'type': 'string', 'minLength': 1},
            'enabled': {'type': 'boolean', 'nullable': True},
            'heartbeat_ttl_seconds': {'type': 'integer', 'minimum': 1, 'nullable': True},
            'artifact_storage_root': {
                'type': 'string', 'nullable': True,
                'description': (
                    'Optional plot storage root (plot-custody ②). A node '
                    'self-registers on every boot and never supplies this, so '
                    'omitting it is FILL-ONLY: the stored operator value survives. '
                    'Use PATCH /platform/chambers/{chamber_id}/storage-root to '
                    'change or clear it.'
                ),
            },
        },
        'additionalProperties': False,
    },
    'ChamberHeartbeatRequest': {
        'type': 'object',
        'required': ['chamber_id', 'reported_status'],
        'properties': {
            'chamber_id': {'type': 'string', 'minLength': 1},
            # idle/in_use only — OFFLINE is a derived state, never reported (the
            # domain Heartbeat value object rejects it → 400).
            'reported_status': {'type': 'string', 'enum': ['idle', 'in_use']},
            'session_id': {'type': 'string', 'nullable': True},
            'expires_at': {'type': 'string', 'nullable': True},
            # C1 heartbeat-carried progress — a node attaches its measurement
            # progress snapshot ONLY on in_use heartbeats (the domain Heartbeat
            # invariant rejects progress on idle → 400). Optional/null otherwise.
            'progress': {'nullable': True, 'allOf': [{'$ref': '#/schemas/ChamberSessionProgress'}]},
            # M2 — a node MAY attach its latest operational error on any status
            # (orthogonal to progress). It is redacted at the write boundary
            # before persistence (URLs/tokens/paths/device ids stripped).
            'last_error': {'type': 'string', 'nullable': True},
        },
        'additionalProperties': False,
    },
    'ChamberHeartbeatAck': {
        'type': 'object',
        'required': ['chamber_id', 'reported_status', 'occurred_at'],
        'properties': {
            'chamber_id': {'type': 'string'},
            'reported_status': {'type': 'string'},
            'occurred_at': {'type': 'string'},
            'session_id': {'type': 'string', 'nullable': True},
        },
        'additionalProperties': False,
    },
    'ChamberResultIngestionRequest': {
        'type': 'object',
        'required': ['schema_version', 'batch_id', 'chamber_id', 'provider_id', 'events'],
        'properties': {
            'schema_version': {'type': 'string', 'const': CHAMBER_RESULT_INGESTION_SCHEMA_VERSION},
            'batch_id': {'type': 'string', 'minLength': 1},
            'chamber_id': {'type': 'string', 'minLength': 1},
            'provider_id': {'type': 'string', 'minLength': 1},
            'events': {
                'type': 'array',
                'minItems': 1,
                'items': {
                    'type': 'object',
                    'required': ['event_id', 'payload'],
                    'properties': {
                        'event_id': {'type': 'integer', 'minimum': 1},
                        'payload': {'type': 'object', 'additionalProperties': True},
                    },
                    'additionalProperties': False,
                },
            },
        },
        'additionalProperties': False,
    },
    # plot-dual-custody ① (2026-08-09) — 노드가 보고하는 보관 관측 스냅샷.
    #
    # **이것은 판정 요청이 아니라 판정 보고다.** 중앙은 원본 보관소를 열 수 없으므로
    # 재판정하지 않고, 상태 토큰·개수·관측 시각·관측한 루트를 그대로 받는다.
    #
    # ``observed_at`` 이 계약의 핵심이다 — 중앙 수신 시각이 아니라 **노드가 실제로
    # 디스크를 열어본 시각**이고, 저장은 이 값 기준 latest-wins 다. 재시도로 늦게
    # 도착한 낡은 관측이 새 판정을 덮으면 화면이 과거로 되돌아간다.
    #
    # ``roots`` 를 함께 싣는 이유: 어디를 보고 그렇게 말하는지 알 수 없으면 시험원이
    # 판정을 반박할 수 없다("옮겼는데 왜 없다고 하지?").
    # plot-custody ② — 챔버별 저장 위치.
    #
    # PATCH 이고 필드가 하나뿐인데도 **생략과 null 을 구분**한다(프로젝트 메타 PATCH 와
    # 같은 규약): 생략 = 불변, null = 삭제. 하나뿐일 때 구분을 접어두면 필드가 둘이 되는
    # 순간 규약이 갈라진다.
    'UpdateChamberStorageRootRequest': {
        'type': 'object',
        'properties': {
            'artifact_storage_root': {
                'type': 'string',
                'nullable': True,
                'description': (
                    'Where this chamber PC must write measurement plots. A UNC '
                    'share is what the audit will look at; a local drive means '
                    'someone still has to move the files by hand — the node warns '
                    'but does not block, because blocking would stop measurement. '
                    'Send null to clear it; omit the field to leave it unchanged. '
                    'Cleared or unset, the node falls back to the workbook Save '
                    'Data "File Structure" cell, which is the pre-2026-08-09 '
                    'behaviour.'
                ),
            },
        },
        'additionalProperties': False,
    },
    'UpdateChamberWebSessionApprovalRequest': {
        'type': 'object',
        'properties': {
            'accepts_web_sessions': {
                'type': 'boolean',
                'nullable': True,
                'description': (
                    'Whether this chamber is approved to accept web sessions. '
                    'Send true/false to record the ruling; send null to withdraw '
                    'it back to "nobody has decided" (which is NOT the same as '
                    'false); omit the field to leave it unchanged. This records '
                    'approval only — it does not start or stop anything, and it '
                    'does not make the node serve. Whether the node actually opened '
                    'a listener is observed from its heartbeat, and the mismatch '
                    'between the two is what an operator reads.'
                ),
            },
        },
        'additionalProperties': False,
    },
    'ChamberSettings': {
        'type': 'object',
        'required': ['chamber_id'],
        'properties': {
            'chamber_id': {'type': 'string'},
            'artifact_storage_root': {'type': 'string', 'nullable': True},
            # SPLIT-6 ② — additive, NOT required. A node built before this field
            # existed keeps parsing this envelope unchanged; one that knows the
            # field merges it over the workbook Chamber Config sheet.
            'equipment_config': {'$ref': '#/schemas/EquipmentConfigMap'},
        },
        'additionalProperties': False,
    },
    # The platform does not know these keys and must not learn them. The keys are
    # whatever the provider's UI descriptor declared (`equipment[].fields`) and
    # whatever that provider's node resolves them against; promoting any of them
    # to a named property would put provider vocabulary in the shared contract,
    # which is exactly what ADR-0018 D-6's three-axis split forbids.
    #
    # `additionalProperties` is spelled out on purpose. A bare `{'type':'object'}`
    # is "any object" in JSON Schema but renders as `Record<string, never>` in
    # TypeScript — a type no value satisfies — so the screen would be unable to
    # send anything but `{}`.
    'EquipmentConfigMap': {
        'type': 'object',
        'description': (
            "A chamber's instrument connection settings as an opaque map. Keys "
            'are declared by the provider UI descriptor; the platform stores and '
            'returns them without interpreting either key or value.'
        ),
        'additionalProperties': {'type': 'string'},
    },
    'ChamberEquipmentConfig': {
        'type': 'object',
        'required': ['chamber_id', 'equipment_config'],
        'properties': {
            'chamber_id': {'type': 'string'},
            'equipment_config': {'$ref': '#/schemas/EquipmentConfigMap'},
            'updated_at': {'type': 'string', 'nullable': True},
        },
        'additionalProperties': False,
    },
    'UpdateChamberEquipmentConfigRequest': {
        'type': 'object',
        'required': ['equipment_config'],
        'properties': {
            'equipment_config': {
                'type': 'object',
                'description': (
                    'Per-KEY patch, not a replacement. A key that is absent is '
                    'left unchanged; a key sent as null is deleted; a key sent '
                    'as a string is set. The merge happens server-side inside '
                    'one transaction, which is what lets two testers edit two '
                    'different fields of the same chamber at the same time '
                    'without either losing the other — so send only the fields '
                    'the operator actually edited. Sending every rendered field '
                    'reintroduces the lost update this shape exists to prevent.'
                ),
                'additionalProperties': {'type': 'string', 'nullable': True},
            },
        },
        'additionalProperties': False,
    },
    'ChamberResultIngestionReceipt': {
        'type': 'object',
        'required': [
            'schema_version', 'batch_id', 'chamber_id',
            'accepted_event_ids', 'failed_event_ids', 'received_at',
            'sync_status', 'readiness_code', 'readiness_reason', 'retryable',
        ],
        'properties': {
            'schema_version': {'type': 'string', 'const': CHAMBER_RESULT_INGESTION_SCHEMA_VERSION},
            'batch_id': {'type': 'string'},
            'chamber_id': {'type': 'string'},
            'accepted_event_ids': {'type': 'array', 'items': {'type': 'integer'}},
            'failed_event_ids': {'type': 'array', 'items': {'type': 'integer'}},
            'received_at': {'type': 'string'},
            'sync_status': {'type': 'string', 'enum': ['enabled', 'disabled']},
            'readiness_code': {
                'type': 'string',
                'enum': list(CENTRAL_SYNC_READINESS_CODE_VALUES),
            },
            'readiness_reason': {'type': 'string'},
            'retryable': {'type': 'boolean'},
        },
        'additionalProperties': False,
    },
    # 멀티챔버 P5 — 중앙 측정 프록시 request/response envelopes. project/sample are
    # required; the server builds the immutable snapshot and the browser cannot
    # substitute model/sample display strings.
    'StartChamberMeasurementRequest': {
        'type': 'object',
        'required': ['project_id', 'sample_id'],
        'properties': {
            'project_id': {'type': 'string', 'format': 'uuid'},
            'sample_id': {'type': 'string', 'format': 'uuid'},
            'published_plan_id': {'type': 'string', 'nullable': True},
            'reference_consumer_provider_id': {
                'type': 'string',
                'minLength': 1,
                'description': (
                    'Natural provider id of the consuming Session node. The '
                    'server uses it only for compatibility checks.'
                ),
            },
            'project_result_reference_requests': {
                'type': 'array',
                'description': (
                    'Revision identities to resolve before equipment activity. '
                    'The browser cannot provide hashes, source ids, payload, or '
                    'the resulting snapshot.'
                ),
                'items': {
                    'type': 'object',
                    'required': ['revision_id', 'reference_type', 'schema_version'],
                    'properties': {
                        'revision_id': {'type': 'string', 'format': 'uuid'},
                        'reference_type': {'type': 'string', 'minLength': 1},
                        'schema_version': {'type': 'string', 'minLength': 1},
                    },
                    'additionalProperties': False,
                },
            },
        },
        'additionalProperties': False,
    },
    'ChamberSessionProgress': {
        'type': 'object',
        'required': ['is_running', 'completed', 'total', 'ratio'],
        'properties': {
            'is_running': {'type': 'boolean'},
            'completed': {'type': 'integer'},
            'total': {'type': 'integer'},
            'ratio': {'type': 'number'},
        },
        'additionalProperties': False,
    },
    'ChamberMeasurementSnapshot': {
        'type': 'object',
        'required': ['chamber_id', 'progress'],
        'properties': {
            'chamber_id': {'type': 'string'},
            'progress': {'$ref': '#/schemas/ChamberSessionProgress'},
        },
        'additionalProperties': False,
    },
    # 멀티챔버 P7/B4 — 중앙 진행 릴레이 WS fan-out 메시지 wire shape. 노드 Session API
    # WS 의 ``{kind, payload}`` 와 동형이되, 챔버 식별을 위해 chamber_id/session_id/
    # occurred_at 을 1급 필드로 운반한다. progress 는 C1 ``ChamberSessionProgress`` 재사용
    # (신규 codegen 타입 0). 도메인 SSOT: ``ChamberProgressEvent.as_wire()``.
    'ChamberProgressEvent': {
        'type': 'object',
        'required': ['kind', 'chamber_id', 'progress'],
        'properties': {
            'kind': {'type': 'string', 'const': 'chamber_progress'},
            'chamber_id': {'type': 'string'},
            'progress': {'$ref': '#/schemas/ChamberSessionProgress'},
            'session_id': {'type': 'string', 'nullable': True},
            'occurred_at': {'type': 'string', 'nullable': True},
        },
        'additionalProperties': False,
    },
}


OPERATIONS: dict[str, dict] = {
    # 멀티챔버 P2 — chamber availability read + registry/heartbeat writes.
    'list_chambers': _operation(
        request=None,
        response='ChamberAvailabilityList',
        permission=PERMISSIONS['list_chambers'],
    ),
    'register_chamber': _operation(
        request='RegisterChamberRequest',
        response='ChamberNodeEnvelope',
        permission=PERMISSIONS['register_chamber'],
    ),
    # 부채 청산 M2 (2026-07-30) — 미등록 chamber_id 로의 heartbeat 는 클라이언트가
    # 고칠 수 있는 사실(먼저 등록하라)이므로 404 다. 서버 장애(503)와 섞이면 운영자가
    # 엉뚱한 시스템을 의심한다. 404 문구는 measurement proxy 와 **같은 SSOT** 를 쓴다.
    'push_chamber_heartbeat': _operation(
        request='ChamberHeartbeatRequest',
        response='ChamberHeartbeatAck',
        permission=PERMISSIONS['push_chamber_heartbeat'],
        error_responses={'404': _CHAMBER_NOT_FOUND_404},
    ),
    # 멀티챔버 P5 — 중앙 측정 프록시.
    'start_chamber_measurement': _operation(
        request='StartChamberMeasurementRequest',
        response='ChamberMeasurementSnapshot',
        permission=PERMISSIONS['start_chamber_measurement'],
        error_responses={
            '404': _CHAMBER_NOT_FOUND_404,
            '409': _CHAMBER_NOT_IDLE_409,
            '503': _CHAMBER_FORWARD_503,
        },
    ),
    'push_chamber_result_ingestion': _operation(
        request='ChamberResultIngestionRequest',
        response='ChamberResultIngestionReceipt',
        permission=PERMISSIONS['push_chamber_result_ingestion'],
        error_responses={
            '400': 'Result-ingestion envelope failed schema validation.',
        },
    ),
    # plot-dual-custody ① (2026-08-09) — 보관 판정 수신/조회.
    #
    # 수신이 노드-스코프인 것은 권한 편의가 아니라 **판정 소재**의 귀결이다. 중앙은
    # 회사 파일서버도 챔버 PC 로컬도 열 수 없으므로 보관 여부를 알 수 없고, 아는 쪽이
    # 말해야 한다. 참조 데이터가 중앙→로컬 PULL 인 것과 방향이 반대인 이유가 그것이다.
    'update_chamber_storage_root': _operation(
        request='UpdateChamberStorageRootRequest',
        response='ChamberNodeEnvelope',
        permission=PERMISSIONS['update_chamber_storage_root'],
        error_responses={'404': _CHAMBER_NOT_FOUND_404},
    ),
    'update_chamber_web_session_approval': _operation(
        request='UpdateChamberWebSessionApprovalRequest',
        response='ChamberNodeEnvelope',
        permission=PERMISSIONS['update_chamber_web_session_approval'],
        error_responses={'404': _CHAMBER_NOT_FOUND_404},
    ),
    'get_chamber_settings': _operation(
        request=None,
        response='ChamberSettings',
        permission=PERMISSIONS['get_chamber_settings'],
        error_responses={'404': _CHAMBER_NOT_FOUND_404},
    ),
    # SPLIT-6 ② — the operator-facing door onto the same stored value. Separate
    # from get_chamber_settings because that one is bound to the calling node's
    # own chamber_id; widening it would let node A read node B's settings.
    'get_chamber_equipment_config': _operation(
        request=None,
        response='ChamberEquipmentConfig',
        permission=PERMISSIONS['get_chamber_equipment_config'],
        error_responses={'404': _CHAMBER_NOT_FOUND_404},
    ),
    'update_chamber_equipment_config': _operation(
        request='UpdateChamberEquipmentConfigRequest',
        response='ChamberEquipmentConfig',
        permission=PERMISSIONS['update_chamber_equipment_config'],
        error_responses={
            '400': (
                'Request body omitted equipment_config, or a value was neither '
                'a string nor null.'
            ),
            '404': _CHAMBER_NOT_FOUND_404,
        },
    ),
    'get_chamber_measurement_progress': _operation(
        request=None,
        response='ChamberMeasurementSnapshot',
        permission=PERMISSIONS['get_chamber_measurement_progress'],
        error_responses={
            '404': _CHAMBER_NOT_FOUND_404,
            '503': _CHAMBER_FORWARD_503,
        },
    ),
    # 멀티챔버 P7/B4 — WS fan-out 구독. response 는 서버가 보내는 진행 이벤트 메시지
    # 스키마(AsyncAPI 메시지 catalog 소스). HTTP responses 빌더는 WEBSOCKET 을 건너뛰므로
    # 미사용 — AsyncAPI 빌더만 이 schema 를 참조한다.
    'subscribe_chamber_progress': _operation(
        request=None,
        response='ChamberProgressEvent',
        permission=PERMISSIONS['subscribe_chamber_progress'],
    ),
}
