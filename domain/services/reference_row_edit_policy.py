"""참조 리비전의 **행 추가·삭제** 판정 (순수 도메인, Wave B-2, 2026-08-11).

## 이것은 값 편집 정책의 완화가 아니다

``reference_entry_edit_policy`` 는 식별 필드를 옮기는 편집을 거부하고, 그 사유를
*"추가+삭제이지 편집이 아니다"* 라고 적는다. **행 추가·삭제는 그 거부가 가리키던 바로 그
연산**이다. 값 편집을 느슨하게 만들어 이 일을 시키면 두 결함이 서로를 가린다 — 오타로 식별
칸을 건드린 편집과, 의도한 행 교체가 같은 요청으로 보이게 된다. 그래서 값 편집 정책은
한 줄도 바뀌지 않고, 이 축은 **자기 operation** 으로 열린다.

## 서버가 정체성을 민팅한다

``reference_id``/``identity_key``/``content_sha256`` 은 전부 payload 의 **파생값**이다
(``Derived-Value No-Client-Recompute SSOT``). 클라이언트가 보내게 두면 저장된
``identity_key`` 가 그 행을 설명하지 않는 상태를 만들 수 있고, 그 어긋남은 투영이 런타임
테이블을 채울 때에야 — 즉 측정 경로에서 — 드러난다. 그러므로 요청은 **payload 만** 나르고
정체성은 도메인 SSOT(``identity_key_for``/``build_reference_entry_hash``)가 만든다.

## entry_order 는 이어 붙이고, 삭제해도 재번호하지 않는다

``ux_reference_entries_revision_order`` 는 ``(revision_id, entry_order)`` **유일성**만
요구하고 밀집성은 요구하지 않는다. 삭제 후 재번호는 남은 전 행을 다시 쓰는 일이고
(16k 행 표에서 성립하지 않는다) 얻는 것이 없다 — 읽기 어댑터는 ``ORDER BY entry_order``
로 **순서**만 쓴다.

## 거부는 전부 typed 이고 조용한 성공이 없다

모르는 행을 지우라는 요청을 조용히 건너뛰면 화면은 "저장됨"이라 말하고 시험원이 지웠다고
믿는 행이 남는다. 같은 이유로 정체성이 겹치는 추가도 거부한다 — 덮어쓰면 두 행 중 어느
쪽이 살아남았는지 아무도 모른다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from domain.models.reference_catalog import CatalogFamily
from domain.services.reference_entry_edit_policy import (
    MAX_ENTRY_EDITS_PER_REQUEST,
    validate_entry_payload_shape,
)
from domain.services.reference_hashing import build_reference_entry_hash
from domain.services.reference_ownership_policy import (
    identity_key_for,
    projection_fields_for,
)

__all__ = [
    'MAX_ROW_EDITS_PER_REQUEST',
    'MintedEntry',
    'ReferenceRowEditError',
    'RowEditOutcome',
    'apply_row_edits',
    'mint_entry',
]

#: 한 요청이 건드릴 수 있는 행 수 상한. 값 편집과 **같은 상수**를 쓴다 — 둘은 같은
#: 표의 같은 쓰기이고, 상한이 둘이면 어느 쪽이 먼저 걸리는지가 요청 모양에 따라 달라져
#: 운영자가 규칙을 예측할 수 없다.
MAX_ROW_EDITS_PER_REQUEST = MAX_ENTRY_EDITS_PER_REQUEST


class ReferenceRowEditError(ValueError):
    """행 추가·삭제 요청이 계약을 벗어남 → 400.

    ``ValueError`` 파생이라 라우트 경계의 기존 매핑(VALIDATION_ERROR)을 그대로 탄다 —
    새 ``ErrorCode`` 를 만들지 않는다(같은 부류의 사실이다: 요청이 틀렸다).
    """


@dataclass(frozen=True)
class MintedEntry:
    """서버가 정체성을 붙인 새 행."""

    reference_id: str
    identity_key: str
    payload: Mapping[str, Any]
    content_sha256: str

    def to_entry(self) -> dict:
        """쓰기 어댑터가 받는 모양(기존 ``create_candidate`` 엔트리와 동형)."""
        return {
            'reference_id': self.reference_id,
            'identity_key': self.identity_key,
            'payload': dict(self.payload),
            'test_condition_ids': [],
            'effective_from': None,
            'effective_to': None,
            # 워크북 좌표가 없다 — 이 행은 시트에서 온 것이 아니다. 빈 문자열이 아니라
            # None 이어야 "어느 시트 몇 행"이라는 없는 사실을 주장하지 않는다.
            'source_sheet_name': None,
            'source_row_number': None,
            'content_sha256': self.content_sha256,
        }


@dataclass(frozen=True)
class RowEditOutcome:
    """판정 결과 — 무엇을 넣고 무엇을 지우는가."""

    additions: tuple[MintedEntry, ...]
    removals: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.additions or self.removals)


def mint_entry(family: CatalogFamily, payload: Any) -> MintedEntry:
    """payload → 정체성이 붙은 행. 모양이 틀리면 문 앞에서 거부한다.

    payload 키 집합이 그 패밀리의 런타임 행과 **정확히 같아야** 한다. payload 는 문서가
    아니라 *런타임 행 그 자체*이므로(``PROJECTION_FIELD_CONTRACT``), 모양이 다르면
    투영이 측정 경로가 읽는 테이블에 망가진 행을 넣는다.
    """
    validate_entry_payload_shape(family, '<new row>', payload)
    expected = tuple(projection_fields_for(family))
    supplied = tuple(payload)
    missing = [field for field in expected if field not in payload]
    extra = [field for field in supplied if field not in expected]
    if missing or extra:
        raise ReferenceRowEditError(
            f'new {family.value} row has the wrong shape — '
            f'missing {missing!r}, unexpected {extra!r}; a reference payload is '
            'the runtime row itself, so it must carry exactly '
            f'{list(expected)!r}'
        )
    try:
        identity_key = identity_key_for(family, payload)
    except KeyError as exc:  # pragma: no cover — shape check above covers it
        raise ReferenceRowEditError(
            f'new {family.value} row is missing identity field {exc}'
        ) from exc
    # ``reference_id`` 는 정체성 문자열 그 자체다 — 워크북 임포터가 세운 규약이고
    # (프론트도 그렇게 읽는다) 두 번째 식별 어휘를 발명하지 않는다.
    return MintedEntry(
        reference_id=identity_key,
        identity_key=identity_key,
        payload=dict(payload),
        content_sha256=build_reference_entry_hash({
            'identity_key': identity_key,
            'payload': payload,
            'test_condition_ids': (),
            'effective_from': None,
            'effective_to': None,
        }),
    )


def apply_row_edits(
    family: CatalogFamily,
    entries: Sequence[Mapping[str, Any]],
    *,
    additions: Iterable[Mapping[str, Any]],
    removals: Iterable[str],
) -> RowEditOutcome:
    """후보의 현재 행 집합 위에서 추가·삭제 요청을 판정한다.

    ``entries`` 는 저장된 행(``reference_id``/``identity_key`` 보유)이다. 순수 함수 —
    DB 도 시계도 보지 않는다.
    """
    addition_payloads = list(additions)
    removal_ids = list(removals)
    if not addition_payloads and not removal_ids:
        raise ReferenceRowEditError('no row additions or removals were supplied')

    total = len(addition_payloads) + len(removal_ids)
    if total > MAX_ROW_EDITS_PER_REQUEST:
        raise ReferenceRowEditError(
            f'{total} row edits exceed the {MAX_ROW_EDITS_PER_REQUEST} per-request '
            'limit — split the change'
        )

    known = {str(entry['reference_id']): entry for entry in entries}

    seen_removals: set[str] = set()
    for reference_id in removal_ids:
        key = str(reference_id)
        if key in seen_removals:
            raise ReferenceRowEditError(
                f'row {key!r} is named twice for removal — one request, one '
                'intention per row'
            )
        seen_removals.add(key)
        if key not in known:
            # 조용히 건너뛰면 화면은 "저장됨"이라 말하고 시험원이 지웠다고 믿는 행이
            # 남는다. 거부가 유일하게 정직한 답이다.
            raise ReferenceRowEditError(
                f'row {key!r} is not in this revision, so it cannot be removed'
            )

    surviving = {
        str(entry['identity_key'])
        for reference_id, entry in known.items()
        if reference_id not in seen_removals
    }

    minted: list[MintedEntry] = []
    for payload in addition_payloads:
        entry = mint_entry(family, payload)
        if entry.identity_key in surviving:
            raise ReferenceRowEditError(
                f'a row with identity {entry.identity_key!r} already exists in '
                'this revision — adding a second one would leave two rows the '
                'lookup cannot tell apart. Edit the existing row, or remove it '
                'in the same request.'
            )
        surviving.add(entry.identity_key)
        minted.append(entry)

    return RowEditOutcome(
        additions=tuple(minted), removals=tuple(sorted(seen_removals)),
    )
