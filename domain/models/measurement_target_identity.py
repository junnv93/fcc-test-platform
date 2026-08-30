# domain/models/measurement_target_identity.py
"""Measurement-target identity value object — domain SSOT.

DB 정체성 키 전환 sprint (2026-05-26) — 측정 결과 DB 의 정체성을 **파일명이
아니라 측정 대상(무엇을 측정했나)** 으로 잡기 위한 단일 진실.

배경
----
local SQLite DB 는 그동안 Excel 파일명(stem)에 결합돼 있었다
(``get_db_path`` = ``{stem}.fcc.db``). 그래서 파일명을 바꾸면 같은 측정인데도
시스템이 새 파일로 인식해 기존 결과 DB 가 고아가 됐고, central 동기화의
session uuid 도 그 fragility 를 그대로 물려받았다. 파일명은 사용자가 바꿀 수
있는 라벨일 뿐, 정체성은 측정 대상으로 잡아야 한다.

측정 대상 정체성 = ``Model Number`` + ``Sample No`` (사용자 확정, 2026-05-26)
- Model Number 는 글로벌 유니크(SM-S921U 등 제품 식별자) → Project Code 불필요.
- Sample 은 시료별 테스트 묶음 분담(A=Conducted, B=Radiated, C=UNII)이라 필수.
- ``User ID`` 는 정체성이 아니라 provenance("누가")로 ``recorded_by`` 에 행별 기록.

이 값 객체 하나가 세 소비자의 SSOT 다:
1. local 매칭 — ``identity_key()`` 로 "같은 측정 대상" 판단 (파일명 무관).
2. DB 파일 이름 — ``db_filename()`` 로 가독성 라벨 ``{model}__{sample}.fcc.db``.
3. central session 정체성 입력 — ``identity_key()`` (provider 무관 → cross-PC 통합).
   uuid5 씨드와 ``provider_session_id`` 자연키 **양쪽**에 들어가며, 파생은
   ``domain/services/central_session_identity.py`` 가 소유한다.

   ⚠️ 이 항목은 2026-08-10 까지 **선언만 있고 배선되지 않았다** — 중앙 세션 정체성은
   ``local_session_id`` 정수만 보았고, 그래서 같은 챔버의 두 측정 대상이 중앙에서 한
   행으로 붕괴했다. 다음 세션이 이 문장을 다시 믿을 수 있도록, 선언과 실제가 일치하는지는
   ``tests/test_central_session_identity.py`` 가 단언한다.

정규화 (Why)
-----------
같은 측정 대상이 표기 차이(대소문자/공백)로 갈리면 안 된다 (``'SM-S921U'`` 와
``'sm-s921u '`` 는 같은 대상). 그래서 매칭/파일명 파생 전에 trim + 내부 공백
단일화 + 대문자로 정규화한다. Model Number/Sample No 는 ASCII 영숫자 도메인이라
``upper()`` 로 충분하고 가독성도 좋다.

Boundary 규칙
------------
Save Data 시트 어휘(``'Model Number'`` / ``'Sample No'``)는 도메인에 새지 않게
한다. application/infrastructure 경계에서 그 dict 를 풀어
``MeasurementTargetIdentity(model_number=..., sample_no=...)`` 로 생성한다.

Domain purity contract
----------------------
- ``frozen=True`` value semantics — equality + hashability.
- stdlib only — ``infrastructure`` / ``pandas`` / ``PySide6`` import 0 (``TestDomainPurity``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


__all__ = [
    'MeasurementTargetIdentity',
    'normalize_identity_component',
    'sanitize_filename_component',
]


#: 파일명으로 쓸 수 없는 문자(Windows/POSIX 공통 금지문자 + 제어문자). 라벨
#: 생성 시에만 사용 — 정체성 매칭은 정규화 값(``identity_key``)으로 하므로
#: sanitize 결과가 충돌해도 내용 기반 발견으로 재연결된다.
_FILENAME_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def normalize_identity_component(value: object) -> str:
    """측정 대상 식별자 1요소(Model/Sample)를 정규화한다 — 정합 SSOT.

    trim + 내부 연속 공백 단일화 + 대문자. ``str.split()`` 은 앞뒤·내부의
    모든 공백 run 을 분리하므로 ``' '.join(...)`` 로 단일 공백 재조립한다.
    ``None`` / 빈값 → ``''``.
    """
    if value is None:
        return ''
    return ' '.join(str(value).split()).upper()


def sanitize_filename_component(value: str) -> str:
    """정규화된 식별자를 파일명 안전 토큰으로 — 금지문자/공백 → ``'_'``."""
    safe = _FILENAME_FORBIDDEN.sub('_', value)
    return safe.replace(' ', '_')


@dataclass(frozen=True)
class MeasurementTargetIdentity:
    """측정 대상의 불변 정체성 — (Model Number, Sample No).

    raw 입력을 그대로 보관하고(원문 보존), 정규화/파생은 property/method 로
    노출한다. 두 측정 대상이 같은지는 ``identity_key()`` 동등성으로 판단한다.
    """

    model_number: str = ''
    sample_no: str = ''

    @property
    def normalized_model(self) -> str:
        return normalize_identity_component(self.model_number)

    @property
    def normalized_sample(self) -> str:
        return normalize_identity_component(self.sample_no)

    @property
    def is_complete(self) -> bool:
        """model+sample 둘 다 정규화 후 non-empty 여야 정체성이 성립한다.

        불완전하면 caller 가 ``{excel_stem}.fcc.db`` fallback + loud warning
        으로 처리한다 (silent 금지 — 측정 대상 미식별은 운영자가 알아야 함).
        """
        return bool(self.normalized_model) and bool(self.normalized_sample)

    def identity_key(self) -> str:
        """정합 키 — local 매칭 + central uuid5 name 의 단일 입력.

        ``'NORMMODEL|NORMSAMPLE'``. ``'|'`` 는 파일명 금지문자이자 식별자에
        나타나지 않으므로 model/sample 경계 구분자로 안전하다.
        """
        return f'{self.normalized_model}|{self.normalized_sample}'

    def db_filename(self) -> str:
        """가독성 DB 파일명 라벨 ``'{model}__{sample}.fcc.db'``.

        불완전한 정체성으로는 안정적 이름을 만들 수 없으므로 fail-loud.
        구분자 ``'__'`` (model 내부 공백/금지문자는 단일 ``'_'`` 로 치환되므로
        경계의 ``'__'`` 와 모호하지 않다).
        """
        if not self.is_complete:
            raise ValueError(
                'incomplete measurement target identity — '
                f'model_number={self.model_number!r}, sample_no={self.sample_no!r}; '
                'caller must fall back to the excel-stem db name with a loud warning'
            )
        model = sanitize_filename_component(self.normalized_model)
        sample = sanitize_filename_component(self.normalized_sample)
        return f'{model}__{sample}.fcc.db'
