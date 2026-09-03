"""보관 판정의 **관측 스냅샷** 어휘 — 판정 하나가 아니라 "언제 어디를 보고 그렇게
판정했나"를 나른다 (plot-dual-custody ① 조회 축).

왜 판정만으로는 부족한가
------------------------

직전 웨이브의 판정(``CustodyFinding``)은 *지금 이 순간* 관측한 결과다. 그것을 그대로
저장해 몇 주 뒤 화면에 띄우면 **정상 워크플로가 사고처럼 보인다**: 시험원은 로컬에
저장한 뒤 수동으로 파일서버에 옮기는데, 측정 직후 1회 관측은 그 이동 **전**이라
영원히 ``MISSING`` 이다. 그것은 직전 웨이브가 상환한 거짓 ``MISSING`` CRITICAL 을
다른 자리에서 재생산하는 것이다.

그래서 저장되는 것은 판정이 아니라 **관측 스냅샷**이다. 세 가지를 함께 나른다:

``observed_at``
    언제 본 것인가. 화면은 판정과 함께 이 값을 보여준다 — 낡은 판정을 현재 사실처럼
    보여주지 않기 위해서다. 참조 데이터 복제본의 watermark 와 같은 역할이다.

``roots``
    **어디를** 보고 그렇게 말하는가. 이것이 없으면 시험원이 판정을 반박할 수 없다
    ("옮겼는데 왜 없다고 하지?" → 어느 루트를 봤는지 알아야 답이 나온다).

``counts``
    상태별 개수. 집계는 **차단 상태를 흡수하지 않는다** — 한 세션이라도 ``MISSING``
    이면 프로젝트도 ``MISSING`` 이다. 다수결이나 비율로 접으면 "98% 이관됨"이 통과로
    읽히는데, 심사에서 빠진 2% 는 100% 문제다.

도메인 순수 — stdlib only. 파일시스템/네트워크/ORM 접근 0.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

from fcc_test_kernel.domain.models.artifact_custody import CustodyStatus


__all__ = [
    'MAX_OBSERVATION_SKEW_SECONDS',
    'observation_is_plausible',
    'CustodySessionSnapshot',
    'CustodyRollup',
    'roll_up_sessions',
    'worst_status',
    'counts_from_statuses',
    'CUSTODY_STATUS_SEVERITY',
]


#: 상태 심각도 SSOT — 집계가 "가장 나쁜 것"을 고를 때 쓰는 유일한 순서.
#:
#: ``MISSING`` 이 ``DIVERGED`` 보다 나쁘지 않다(둘 다 차단이고 둘 다 재측정 없이는
#: 못 고칠 수도 있다). 순서를 매기는 목적은 등급이 아니라 **접기**이므로, 차단 둘을
#: 같은 층에 두고 그중 먼저 만난 것을 고르는 대신 결정적으로 하나를 고른다.
#: ``UNKNOWN`` 은 ``VERIFIED`` 보다 나쁘다 — 모르는 것을 괜찮은 것으로 접지 않는다.
CUSTODY_STATUS_SEVERITY: Mapping[str, int] = {
    CustodyStatus.VERIFIED.value: 0,
    CustodyStatus.UNKNOWN.value: 1,
    CustodyStatus.DIVERGED.value: 2,
    CustodyStatus.MISSING.value: 3,
}


def worst_status(counts: Mapping[str, int]) -> CustodyStatus:
    """상태별 개수 → 그 집합을 대표하는 **가장 나쁜** 상태.

    개수가 전부 0 이면 ``UNKNOWN`` 이다 — **``VERIFIED`` 가 아니다.** 볼 것이
    없었다는 사실을 "다 괜찮다"로 접으면, 아티팩트를 하나도 적재하지 못한 세션이
    화면에서 초록으로 보인다.
    """
    present = [
        status for status, severity in sorted(
            CUSTODY_STATUS_SEVERITY.items(), key=lambda kv: -kv[1],
        )
        if int(counts.get(status, 0) or 0) > 0
    ]
    if not present:
        return CustodyStatus.UNKNOWN
    return CustodyStatus(present[0])


#: 관측 시각이 현재보다 이만큼 넘게 **미래**면 시계가 어긋난 것으로 본다.
#:
#: ⚠️ 이 상한이 없으면 시계 스큐 한 번이 판정을 **영구히 얼린다.** ``observed_at`` 은
#: latest-wins 의 축이고 비교가 엄격 ``>`` 이므로, 한 번 먼 미래 값이 들어가면 그 뒤의
#: **정직한 관측이 전부 밀려난다** — 노드 저널에서도, 중앙에서도(적대적 리뷰 2026-08-09).
#: 미래 방향만 보는 이유: 과거 시각은 정당하다(쌓인 저널 행이 뒤늦게 나가는 것이 정상).
MAX_OBSERVATION_SKEW_SECONDS = 3600


def observation_is_plausible(
    observed_at, now, *, max_skew_seconds: int = MAX_OBSERVATION_SKEW_SECONDS,
) -> bool:
    """관측 시각이 현재 대비 그럴듯한가 (미래 방향만).

    두 인자 모두 aware ``datetime`` 이다 — **도메인은 파싱하지 않는다**. 각 계층이
    자기 파서 SSOT 로 해소한 뒤 이 판정을 부른다(두 번째 타임스탬프 파서 금지).
    """
    if observed_at is None or now is None:
        return False
    return (observed_at - now).total_seconds() <= max_skew_seconds


@dataclass(frozen=True)
class CustodySessionSnapshot:
    """한 세션을 **한 번 관측한** 결과.

    ``findings`` 는 **조치 가능한 것만** 담는다(비-``VERIFIED``). ``VERIFIED`` 개수는
    ``counts`` 에 남으므로 무엇이 숨겨진 것이 아니고, 세션당 ≈1,000장을 그대로 나르면
    전송·저장이 개수에 비례해 커지는데 그 목록으로 할 수 있는 일이 없다.
    """

    session_id: str
    observed_at: str
    counts: Mapping[str, int] = field(default_factory=dict)
    #: 관측 시 실제로 열어본 루트들. 빈 튜플이면 볼 곳이 없었다는 뜻이다.
    roots: tuple[str, ...] = ()
    #: 조치 가능한 항목(상대주소 · 상태 · 사유). 도메인은 shape 만 정하고 값은 어댑터가 채운다.
    findings: tuple[Mapping[str, object], ...] = ()
    #: 노드가 아는 세션 표시 이름(모델/샘플 등). 판정에 쓰이지 않는다.
    label: str = ''

    @property
    def status(self) -> CustodyStatus:
        return worst_status(self.counts)

    @property
    def total(self) -> int:
        return sum(int(v or 0) for v in self.counts.values())

    @property
    def is_blocking(self) -> bool:
        """이 세션이 성적서 발행을 막는 상태인가 (``CustodyFinding.is_blocking`` 과 동형)."""
        return self.status in (CustodyStatus.MISSING, CustodyStatus.DIVERGED)


@dataclass(frozen=True)
class CustodyRollup:
    """여러 세션 스냅샷을 한 프로젝트로 접은 결과.

    ``oldest_observed_at`` 을 나르는 이유: 프로젝트가 초록이어도 그 근거가 3주 전
    관측이면 그것은 "지금 괜찮다"가 아니다. **가장 낡은** 관측이 그 집합 전체의
    신뢰 수명을 결정하므로 최신이 아니라 최고령을 보여준다.
    """

    counts: Mapping[str, int] = field(default_factory=dict)
    session_count: int = 0
    blocking_session_count: int = 0
    oldest_observed_at: Optional[str] = None
    newest_observed_at: Optional[str] = None
    #: 프로젝트에 아직 귀속되지 않은(=조인키 미해소) 세션 수. **조용히 0 이 아니다.**
    unresolved_session_count: int = 0
    #: 프로젝트 세션에는 있으나 보관 스냅샷 자체가 없는 수. unresolved와 합치지 않는다.
    missing_snapshot_session_count: int = 0

    @property
    def status(self) -> CustodyStatus:
        status = worst_status(self.counts)
        # Existing blocking findings win. A missing snapshot is otherwise an
        # inability to establish custody, not a synthetic MISSING finding.
        if status in (CustodyStatus.MISSING, CustodyStatus.DIVERGED):
            return status
        if self.missing_snapshot_session_count > 0:
            return CustodyStatus.UNKNOWN
        return status

    @property
    def is_blocking(self) -> bool:
        return self.status in (CustodyStatus.MISSING, CustodyStatus.DIVERGED)


def roll_up_sessions(
    snapshots: Sequence[CustodySessionSnapshot],
    *,
    unresolved_session_count: int = 0,
    missing_snapshot_session_count: int = 0,
) -> CustodyRollup:
    """세션 스냅샷들 → 프로젝트 판정 (순수).

    **차단을 흡수하지 않는다.** 개수를 더하고 가장 나쁜 상태를 고른다 — 비율로
    접지 않는다.

    ``unresolved_session_count`` 는 프로젝트에 귀속되지 않아 이 집계에 **들어오지
    못한** 세션 수다. 0 이 아니면 화면이 그 사실을 말해야 한다 — 안 그러면 "이
    프로젝트는 이상 없음"이 "이 프로젝트에서 우리가 볼 수 있는 것은 이상 없음"과
    구분되지 않는다.

    ``missing_snapshot_session_count`` 는 프로젝트 세션 자체는 있으나 보고된
    스냅샷이 없는 수다. 이것은 unresolved snapshot과 합치지 않으며, 기존
    ``MISSING``/``DIVERGED`` finding이 없을 때만 project status를 ``UNKNOWN``으로
    올린다.
    """
    tally = {status.value: 0 for status in CustodyStatus}
    oldest: Optional[str] = None
    newest: Optional[str] = None
    blocking = 0
    for snapshot in snapshots:
        for key, value in (snapshot.counts or {}).items():
            if key in tally:
                tally[key] += int(value or 0)
        if snapshot.is_blocking:
            blocking += 1
        stamp = str(snapshot.observed_at or '').strip()
        if stamp:
            # ISO-8601 문자열은 사전순 비교가 시간순과 일치한다(같은 오프셋 표기 기준).
            # 여기서 파싱하지 않는 이유: 도메인이 두 번째 타임스탬프 파서를 만들면
            # 경계의 SSOT(``envelope_helpers.parse_timestamp``)와 갈라진다.
            oldest = stamp if oldest is None or stamp < oldest else oldest
            newest = stamp if newest is None or stamp > newest else newest
    return CustodyRollup(
        counts=tally,
        session_count=len(snapshots),
        blocking_session_count=blocking,
        oldest_observed_at=oldest,
        newest_observed_at=newest,
        unresolved_session_count=int(unresolved_session_count or 0),
        missing_snapshot_session_count=int(missing_snapshot_session_count or 0),
    )


def counts_from_statuses(statuses: Iterable[str]) -> dict[str, int]:
    """상태 토큰 열 → 개수 맵 (알 수 없는 토큰은 ``UNKNOWN`` 으로 접는다).

    알 수 없는 토큰을 버리지 않는 이유: 버리면 총계가 줄어 "관측된 것이 더 적다"로
    보이고, 그것은 조용한 손실이다. ``UNKNOWN`` 으로 접으면 총계가 보존되고 그 행이
    판정 불가로 표면화된다.
    """
    tally = {status.value: 0 for status in CustodyStatus}
    for raw in statuses:
        token = str(raw or '').strip().lower()
        tally[token if token in tally else CustodyStatus.UNKNOWN.value] += 1
    return tally
