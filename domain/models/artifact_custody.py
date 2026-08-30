"""플롯 보관(custody) 도메인 값 객체 — 원본/사본 이중 보관의 어휘 SSOT.

플롯은 심사 증거다. 심사관이 보는 것은 회사가 믿는 보관소(파일서버 또는 챔버 PC
로컬)이므로 **권위는 거기 있고**, 중앙에 두는 것은 사본이다. 이 모듈은 그 관계를
표현하는 어휘만 소유한다 — 판정은 ``artifact_custody_policy``, 관측은 어댑터다.

세 개념을 **의도적으로 분리**한다:

``CustodyExpectation``
    DB 가 말하는 것 — 있어야 할 상대주소와 그때 계산한 지문. ``measurement_artifacts``
    행에서 파생된다.

``CustodyObservation``
    보관 루트에서 **실제로 본 것**. 어댑터가 파일을 열어 채운다. 판정 함수는 이것을
    만들지 않고 **받는다** — 그래야 판정이 관측 코드를 오라클로 삼지 않는다
    (「An oracle sharing a fixture is not independent」).

``CustodyFinding``
    둘을 대조한 결과. 차단 여부는 소비자(발행 게이트)가 정하고, 이 값 객체는
    **무엇을 보았는지**만 말한다.

도메인 순수 — stdlib only. 파일시스템/네트워크/ORM 접근 0.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, TypeAlias


# Local SQLite sessions are integers; central/provider report sessions may be
# opaque strings.  This is the shared boundary vocabulary for both forms.
SessionIdentifier: TypeAlias = int | str


__all__ = [
    'SessionIdentifier',
    'CustodyAuditNoticeCode',
    'CustodyAuditNotice',
    'CustodyStatus',
    'CustodyExpectation',
    'CustodyObservation',
    'CustodyFinding',
    'CustodyReport',
]


class CustodyAuditNoticeCode(str, Enum):
    """Non-finding observations from an artifact custody audit.

    These notices are deliberately not represented as synthetic findings.  A
    finding changes the custody counts and can therefore change the verdict;
    a notice only says that the audit input or policy had something worth
    surfacing to an operator.
    """

    NO_AUDITED_ARTIFACTS = 'no_audited_artifacts'
    RELATIVE_ROOT_REJECTED = 'relative_root_rejected'


@dataclass(frozen=True)
class CustodyAuditNotice:
    """Typed, non-blocking audit information that is not a custody finding."""

    code: CustodyAuditNoticeCode | str
    detail: str
    candidate: str = ''
    reason: str = ''

    @property
    def code_value(self) -> str:
        """Return the wire/log token without relying on ``Enum.__str__``."""
        return self.code.value if isinstance(self.code, CustodyAuditNoticeCode) else str(self.code)


class CustodyStatus(str, Enum):
    """보관 판정 4-token SSOT.

    ``UNKNOWN`` 이 별도 토큰인 것이 이 열거의 핵심이다. 판정 불가를 ``VERIFIED`` 로
    접으면 **보관 루트 설정 하나가 빠진 것만으로 게이트가 조용히 무력화**되고,
    ``MISSING`` 으로 접으면 루트를 아직 정하지 않은 팀에서 발행이 멈춰 운영자가
    게이트를 꺼버린다. 교정 만료 판정(``equipment_section_policy``)이 같은 이유로
    세 갈래인 것과 같은 구조다.
    """

    #: 있어야 할 자리에 있고 지문도 DB 와 같다.
    VERIFIED = 'verified'
    #: 있어야 할 자리에 없다 — 이관되지 않았거나 삭제됐다.
    MISSING = 'missing'
    #: 있는데 지문이 다르다 — DB 가 말하는 증거와 실물이 다르다.
    DIVERGED = 'diverged'
    #: 판정할 수 없다 — 보관 루트 미설정, 상대주소 부재, 관측 실패.
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class CustodyExpectation:
    """DB 가 말하는 "있어야 할 것".

    ``relative_path`` 는 File Structure 루트 기준 상대주소이고, ``sha256`` 은 캡처
    시점에 계산된 지문이다. 둘 다 ``measurement_artifacts`` 행이 이미 갖고 있다 —
    이 웨이브가 새로 만든 것이 아니라 **맞춰보는 코드만 없었다**.

    ``sha256`` 이 비면 내용 대조를 할 수 없고 존재만 볼 수 있다. 그것은 "같다"가
    아니라 "모른다"이므로 판정은 ``UNKNOWN`` 이다.
    """

    relative_path: str
    sha256: str = ''
    artifact_type: str = ''
    #: 진단 메시지에 사람이 알아볼 이름을 싣기 위한 보조 정보(판정에 쓰이지 않는다).
    label: str = ''
    row_order: Optional[int] = None

    @property
    def is_addressable(self) -> bool:
        """상대주소가 있어야 어느 루트에서든 찾아볼 수 있다."""
        return bool(self.relative_path.strip())

    @property
    def has_digest(self) -> bool:
        return bool(self.sha256.strip())


@dataclass(frozen=True)
class CustodyObservation:
    """보관 루트에서 실제로 본 것.

    ``observed`` 가 False 면 그것이 *부재의 관측*(파일이 없다)인지 *관측의 실패*
    (루트에 접근할 수 없다)인지 구분해야 한다 — 전자는 시험원이 파일을 옮기면 되고
    후자는 설정/권한 문제다. 그래서 ``error`` 를 따로 나른다.
    """

    #: 관측을 시도한 보관 루트(진단용). 빈 문자열이면 루트가 설정되지 않은 것이다.
    root: str = ''
    #: 파일이 그 자리에 실제로 있었는가.
    exists: bool = False
    #: 루트에서 **독립적으로 다시 계산한** 지문. 계산하지 못했으면 빈 문자열.
    sha256: str = ''
    byte_size: Optional[int] = None
    #: 관측 자체가 실패한 사유(권한/네트워크). 부재와 구분된다.
    error: str = ''

    @property
    def is_conclusive(self) -> bool:
        """관측이 판정의 근거가 될 수 있는가.

        루트가 없거나 관측이 실패했으면 무엇도 결론지을 수 없다.
        """
        return bool(self.root.strip()) and not self.error


@dataclass(frozen=True)
class CustodyFinding:
    """기대 × 관측 → 판정 하나."""

    status: CustodyStatus
    expectation: CustodyExpectation
    observation: CustodyObservation
    #: 사람이 읽는 사유. 판정을 재구성하지 않고 그대로 진단/로그에 실린다.
    reason: str = ''

    @property
    def is_blocking(self) -> bool:
        """발행을 막아야 하는가.

        ``UNKNOWN`` 은 막지 않는다 — 판정 불가를 차단으로 접으면 게이트가 꺼진다.
        """
        return self.status in (CustodyStatus.MISSING, CustodyStatus.DIVERGED)

    def describe(self) -> str:
        name = self.expectation.label or self.expectation.relative_path or '(주소 없음)'
        return f'{name}: {self.reason}' if self.reason else name


@dataclass(frozen=True)
class CustodyReport:
    """한 세션(또는 한 성적서)의 보관 판정 모음."""

    findings: tuple[CustodyFinding, ...] = field(default_factory=tuple)
    #: 입력/정책 상태를 surface하되 findings/counts에는 넣지 않는다.
    notices: tuple[CustodyAuditNotice, ...] = field(default_factory=tuple)

    def of_status(self, status: CustodyStatus) -> tuple[CustodyFinding, ...]:
        return tuple(f for f in self.findings if f.status is status)

    @property
    def blocking(self) -> tuple[CustodyFinding, ...]:
        return tuple(f for f in self.findings if f.is_blocking)

    @property
    def counts(self) -> dict[str, int]:
        """상태별 개수 — 구조화 로그의 ``extra`` 에 그대로 실린다."""
        tally = {status.value: 0 for status in CustodyStatus}
        for finding in self.findings:
            tally[finding.status.value] += 1
        return tally

    def summary(self) -> str:
        tally = self.counts
        return ' '.join(f'{key}={tally[key]}' for key in sorted(tally))
