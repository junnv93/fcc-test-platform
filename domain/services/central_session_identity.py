"""중앙 세션 정체성의 측정-대상 축 — 순수 파생 SSOT (2026-08-10).

**무엇을 고치는가.** 챔버 PC 는 측정 대상마다 DB 를 하나씩 두고
(``{model}__{sample}.fcc.db``) 각 DB 는 세션을 1 부터 센다. 그래서
``local_session_id`` 는 같은 챔버 안에서 대상별로 **되풀이된다**. 중앙
``test_sessions`` 에는 그 둘을 가를 컬럼이 없으므로(정체성은
``(provider_id, chamber_id, provider_session_id)`` 뿐), 이 축이 없으면 같은 챔버에서
측정한 두 기기가 **중앙의 한 행으로 붕괴**하고 나중 것의 측정이 먼저 것의 프로젝트로
귀속된다(``project_id`` 는 충돌 시 fill-only 라 먼저 해소된 값이 고정된다).

**왜 도메인인가.** 이름 규칙은 순수 함수이고 소비자가 두 패키지에 걸쳐 있다 —
결과 동기화(``application/headless``)와 보관 축(``application/artifacts``). 한쪽에
두면 다른 쪽이 패키지 경계를 넘어야 하고, 그 경계는 저장소 분리 baseline 이
ratchet-down only 로 지키는 축이다. 순수 규칙은 양쪽이 함께 볼 수 있는 곳에 둔다.

**정규화는 여기서 하지 않는다.** ``MeasurementTargetIdentity`` 가 이미 소유하고
있고, 이 모듈은 그 결정에 위임한다 — 두 번째 정규화 규칙은 조용히 갈라진다.
"""
from __future__ import annotations

from typing import Any

from domain.models.measurement_target_identity import MeasurementTargetIdentity


__all__ = [
    'local_session_id_from_natural_key',
    'measurement_target_key',
    'provider_session_natural_key',
]


def measurement_target_key(model_number: Any, sample_no: Any) -> str:
    """측정 대상 정체성 문자열. 대상이 완전히 식별되지 않으면 ``''``.

    ``is_complete`` 를 게이트로 쓰는 이유는 모델 자신이 그렇게 정의하기 때문이다 —
    절반만 식별된 대상은 두 기기를 가르지 못하므로, 부분 키는 가른 척만 한다.
    """
    identity = MeasurementTargetIdentity(
        model_number=str(model_number or ''), sample_no=str(sample_no or ''),
    )
    return identity.identity_key() if identity.is_complete else ''


def provider_session_natural_key(local_session_id: int, target_identity: Any = '') -> str:
    """``provider_session_id`` — 챔버 스코프 중앙 자연키의 **단일 정의**.

    소비자가 셋이고(결과 동기화 · 보관 발행 · 보관 재관측) 셋은 **정확히 일치해야
    한다**: 보관 스냅샷은 이 문자열로 자기 세션에 조인되므로, 갈라진 사본 하나가
    스냅샷을 중앙에서 미귀속으로 남기는 동안 노드의 테스트는 전부 통과한다.

    정체성이 없으면 오늘 이 코드가 늘 내보내던 값을 그대로 낸다 — 새 정보가 없는
    경우까지 이름을 바꾸면 기존 중앙 행과 갈라질 뿐 얻는 것이 없다.
    """
    local_id = int(local_session_id)
    target = str(target_identity or '').strip()
    if not target:
        return str(local_id)
    return f'{target}:{local_id}'


def local_session_id_from_natural_key(natural_key: Any) -> int | None:
    """자연키에서 **노드-로컬 정수 세션 id** 를 되꺼낸다. 없으면 ``None``.

    **왜 역함수가 필요한가.** 중앙은 성적서 생성용 세션 목록을 만들 때
    ``provider_session_id`` 를 노드 Headless 리포트 API 에 그대로 넘겼다 — 그 API 는
    로컬 SQLite 스코프라 **정수 세션 id** 를 받기 때문이다. 자연키에 측정 대상이
    들어가면서 그 값은 더 이상 정수가 아니고, 정수 파싱에 실패한 행을 조용히 건너뛰던
    옛 코드는 **목록을 통째로 비운다**(아무것도 실패하지 않고 시험원은 "고를 세션이
    없음"을 본다).

    문법을 아는 곳이 하나여야 하므로 역함수는 정방향 옆에 둔다. ``rsplit`` 인 이유는
    대상 키가 콜론을 포함해도 로컬 id 는 **항상 마지막 조각**이기 때문이다.
    """
    text = str(natural_key or '').strip()
    if not text:
        return None
    candidate = text.rsplit(':', 1)[-1]
    try:
        parsed = int(candidate)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
