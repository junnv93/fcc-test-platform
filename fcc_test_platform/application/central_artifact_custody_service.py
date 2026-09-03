"""플롯 보관 현황 — 중앙 수신 + 프로젝트 축 조회 서비스 (plot-dual-custody ①, 2026-08-09).

**중앙은 판정하지 않는다.** 이 서비스가 하는 일은 셋이다: 노드가 보낸 판정을 검증해
저장하고, 프로젝트 축으로 접어서 돌려주고, 한 세션의 상세를 돌려준다. 어느 것도
"플롯이 거기 있나"를 스스로 답하지 않는다 — 중앙은 회사 파일서버도 챔버 PC 로컬도
열 수 없다.

**어휘 검증이 여기 있는 이유(Platform Boundary Honesty).** ``status`` 토큰을 검증하지
않고 DB 로 내려보내면 CHECK 위반이 드라이버 예외가 되고, 그것은 503 으로 나간다 —
즉 **클라이언트 잘못이 중앙 장애로 둔갑한다.** 경계에서 걸러 400 을 낸다.

**집계는 도메인이 한다.** ``roll_up_sessions`` 가 SSOT 이고 이 서비스는 행을 그 모양으로
옮기기만 한다. 여기서 다시 세면 화면의 집계와 발행 게이트의 집계가 갈라질 수 있다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable, Mapping, Optional, Sequence

from fcc_test_kernel.application.central_contract.envelope_helpers import parse_timestamp
from fcc_test_kernel.domain.models.artifact_custody import CustodyStatus
from fcc_test_kernel.domain.models.artifact_custody_snapshot import (
    CustodySessionSnapshot,
    roll_up_sessions,
    worst_status,
)
from fcc_test_platform.domain.ports.output.central_artifact_custody_port import (
    ArtifactCustodyNotFoundError,
    CentralArtifactCustodyError,
)


__all__ = [
    'ArtifactCustodyReportRejected',
    'CentralArtifactCustodyService',
]


#: 허용 상태 토큰 — **도메인 enum 에서 파생**한다(계약 스키마와 같은 소스).
_ALLOWED_STATUSES = frozenset(status.value for status in CustodyStatus)
#: findings 에는 ``VERIFIED`` 가 오지 않는다(조치 가능한 것만 나른다).
_ALLOWED_FINDING_STATUSES = _ALLOWED_STATUSES - {CustodyStatus.VERIFIED.value}
#: 개수 맵의 허용 키 — 상태 토큰과 같은 집합이다.
_COUNT_KEYS = _ALLOWED_STATUSES

#: 개수 상한 — PostgreSQL ``integer`` 범위. 넘으면 DB 가 거절해 클라이언트 잘못이
#: 503(중앙 장애)으로 둔갑한다(Platform Boundary Honesty).
_MAX_COUNT = 2_147_483_647


class ArtifactCustodyReportRejected(ValueError):
    """노드가 보낸 보관 보고가 계약을 만족하지 않는다 (→ 400)."""


class CentralArtifactCustodyService:
    """보관 스냅샷 수신 + 프로젝트 축 조회."""

    def __init__(
        self, *, read_port=None, write_port=None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._read_port = read_port
        self._write_port = write_port
        #: 수신 시각을 찍는 시계. 형제 수신 서비스
        #: (``chamber_result_ingestion_service``)와 **같은 모양**이다 — 같은 이름의
        #: 칸(``received_at``)을 같은 계층에서 찍으므로 두 경계가 서로 다른 규약을
        #: 갖지 않게 한다.
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # ── 수신 ────────────────────────────────────────────────────────────────

    def store_report(
        self, *, chamber_id: str, provider_id: str, sessions: Sequence[Mapping],
    ) -> dict:
        """노드가 보고한 스냅샷들을 latest-wins 로 저장한다.

        ``superseded`` 를 **영수증에 실어 돌려준다** — 밀려남은 실패가 아니지만(재시도가
        순서를 뒤집을 수 있다) 조용해서도 안 된다. 조용히 성공으로 접으면 노드는
        "저장됐다"고 믿는데 중앙은 옛 판정을 갖고 있고, 그 불일치는 아무 데도 드러나지
        않는다.
        """
        if self._write_port is None:
            raise RuntimeError('CentralArtifactCustodyService has no write port')
        # ⚠️ **도착 시각이지 완료 시각이 아니다.** 이름이 ``received_at`` 이므로 쓰기
        # *이전* 에 찍는다 — 느린 쓰기가 도착을 늦게 보이게 하면, 노드가 순서를 뒤집어
        # 보낸 것과 중앙이 느렸던 것을 사후에 구분할 수 없다.
        received_at = self._clock().astimezone(timezone.utc).isoformat()
        prepared = [self._prepare_session(session) for session in sessions]
        receipt = self._write_port.store_report(
            provider_id=provider_id, chamber_id=chamber_id, sessions=prepared,
        )
        # 포트가 돌려준 dict 를 제자리에서 고치지 않는다 — 어댑터가 자기 캐시 행을
        # 돌려주는 구현이면 그 mutation 이 어댑터 안으로 새어 들어간다.
        return {**(receipt or {}), 'received_at': received_at}

    def _prepare_session(self, session: Mapping) -> dict:
        if not isinstance(session, Mapping):
            raise ArtifactCustodyReportRejected('each session entry must be an object')
        session_key = str(session.get('provider_session_id') or '').strip()
        if not session_key:
            raise ArtifactCustodyReportRejected('provider_session_id is required')

        status = str(session.get('status') or '').strip().lower()
        if status not in _ALLOWED_STATUSES:
            raise ArtifactCustodyReportRejected(
                f'unknown custody status {status!r} for session {session_key!r} — '
                f'expected one of {sorted(_ALLOWED_STATUSES)}'
            )

        observed_at = str(session.get('observed_at') or '').strip()
        # 파싱 가능성만 확인하고 **원문을 저장한다** — 여기서 재직렬화하면 노드가 보낸
        # 시각과 중앙이 저장한 시각이 표기 차이로 갈라질 수 있고, latest-wins 비교는
        # 그 값으로 돈다.
        if parse_timestamp(observed_at) is None:
            raise ArtifactCustodyReportRejected(
                f'observed_at {observed_at!r} for session {session_key!r} is not a '
                'parseable timestamp — the latest-wins comparison depends on it'
            )

        counts = session.get('counts') or {}
        if not isinstance(counts, Mapping):
            raise ArtifactCustodyReportRejected('counts must be an object')
        unknown_keys = {str(k) for k in counts} - _COUNT_KEYS
        if unknown_keys:
            raise ArtifactCustodyReportRejected(
                f'unknown count keys {sorted(unknown_keys)} for session {session_key!r}'
            )
        # ⚠️ **키만 검증하고 값을 안 보면 음수 하나가 프로젝트 판정을 뒤집는다.**
        # ``roll_up_sessions`` 는 개수를 더하고 ``worst_status`` 는 ``> 0`` 만 보므로,
        # 한 세션의 ``missing: -3`` 이 다른 세션의 진짜 ``missing: 3`` 을 상쇄해
        # 프로젝트 배지가 **verified** 가 된다(차단 세션이 있는데도). 이 저장소의
        # 서명 결함 부류 — 파이프라인은 완주하고 숫자만 조용히 틀린다.
        # 상한도 본다: 범위를 넘으면 PG 가 거절해 **클라이언트 잘못이 503 으로 둔갑**한다.
        normalized_counts = {}
        for key in _COUNT_KEYS:
            raw = counts.get(key, 0)
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise ArtifactCustodyReportRejected(
                    f'count {key!r} for session {session_key!r} must be an integer, '
                    f'got {type(raw).__name__}'
                )
            if raw < 0 or raw > _MAX_COUNT:
                raise ArtifactCustodyReportRejected(
                    f'count {key!r}={raw} for session {session_key!r} is out of range '
                    f'(0..{_MAX_COUNT})'
                )
            normalized_counts[key] = raw

        # 선언된 status 와 개수가 어긋나면 거절한다. 둘 다 받아 저장하면 한 행이
        # 초록 배지와 '발행 차단'을 **동시에** 보여주고, 정렬(차단 우선)도 어긋난다.
        # 어느 쪽이 참인지 중앙이 고를 수는 없으므로(중앙은 디스크를 못 본다) 노드가
        # 모순된 것을 보냈다는 사실 자체를 400 으로 돌려준다.
        derived = worst_status(normalized_counts).value
        if derived != status:
            raise ArtifactCustodyReportRejected(
                f'session {session_key!r} declares status {status!r} but its counts '
                f'imply {derived!r} — a row cannot be both'
            )

        roots = session.get('roots') or []
        if not isinstance(roots, (list, tuple)):
            raise ArtifactCustodyReportRejected('roots must be an array')

        return {
            'provider_session_id': session_key,
            'status': status,
            'counts': normalized_counts,
            'observed_at': observed_at,
            'session_label': _optional_text(session.get('session_label')),
            'roots_json': json.dumps([str(r) for r in roots], ensure_ascii=False),
            'findings': [
                self._prepare_finding(finding, session_key)
                for finding in (session.get('findings') or [])
            ],
        }

    def _prepare_finding(self, finding: Mapping, session_key: str) -> dict:
        if not isinstance(finding, Mapping):
            raise ArtifactCustodyReportRejected('each finding must be an object')
        relative_path = str(finding.get('relative_path') or '').strip()
        if not relative_path:
            raise ArtifactCustodyReportRejected(
                f'finding without relative_path in session {session_key!r} — a finding '
                'with no address cannot be acted on'
            )
        status = str(finding.get('status') or '').strip().lower()
        if status not in _ALLOWED_FINDING_STATUSES:
            raise ArtifactCustodyReportRejected(
                f'finding status {status!r} is not actionable — findings carry only '
                f'{sorted(_ALLOWED_FINDING_STATUSES)} (verified rows stay in counts)'
            )
        return {
            'relative_path': relative_path,
            'status': status,
            'artifact_type': _optional_text(finding.get('artifact_type')),
            'expected_sha256': _optional_text(finding.get('expected_sha256')),
            'observed_sha256': _optional_text(finding.get('observed_sha256')),
            'reason': _optional_text(finding.get('reason')),
        }

    # ── 조회 ────────────────────────────────────────────────────────────────

    def get_project_custody(self, project_id: str) -> dict:
        """프로젝트 축 요약 + 세션 행.

        집계는 ``roll_up_sessions`` 도메인 SSOT 가 한다 — 여기서 다시 세면 화면의
        판정과 발행 게이트의 판정이 갈라진다.
        """
        if self._read_port is None:
            raise RuntimeError('CentralArtifactCustodyService has no read port')
        rows = self._read_port.list_project_snapshots(project_id) or []
        snapshots = [
            CustodySessionSnapshot(
                session_id=str(row.get('provider_session_id') or ''),
                observed_at=str(row.get('observed_at') or ''),
                counts=_counts_of(row),
                roots=tuple(row.get('roots') or ()),
                label=str(row.get('session_label') or ''),
            )
            for row in rows
        ]
        rollup = roll_up_sessions(
            snapshots,
            unresolved_session_count=self._read_port.count_unresolved_snapshots(
                project_id,
            ),
            missing_snapshot_session_count=self._read_port.count_sessions_without_snapshot(
                project_id,
            ),
        )
        return {
            'project_id': project_id,
            'status': rollup.status.value,
            'counts': dict(rollup.counts),
            'session_count': rollup.session_count,
            'blocking_session_count': rollup.blocking_session_count,
            'unresolved_session_count': rollup.unresolved_session_count,
            'missing_snapshot_session_count': rollup.missing_snapshot_session_count,
            'oldest_observed_at': rollup.oldest_observed_at,
            'newest_observed_at': rollup.newest_observed_at,
            'sessions': [
                _session_summary(row, snapshot)
                for row, snapshot in zip(rows, snapshots)
            ],
        }

    def get_snapshot(self, project_id: str, snapshot_id: str) -> dict:
        if self._read_port is None:
            raise RuntimeError('CentralArtifactCustodyService has no read port')
        row = self._read_port.get_snapshot(project_id, snapshot_id)
        counts = _counts_of(row)
        return {
            'snapshot_id': row.get('snapshot_id'),
            'provider_session_id': row.get('provider_session_id'),
            'chamber_id': row.get('chamber_id'),
            'session_label': row.get('session_label'),
            'status': row.get('status'),
            'counts': counts,
            'observed_at': row.get('observed_at'),
            'reported_at': row.get('reported_at'),
            'roots': list(row.get('roots') or []),
            'findings': [
                {k: v for k, v in finding.items() if v is not None}
                for finding in (row.get('findings') or [])
            ],
        }


def _counts_of(row: Mapping) -> dict:
    return {
        status: int(row.get(f'{status}_count', 0) or 0)
        for status in sorted(_COUNT_KEYS)
    }


def _session_summary(row: Mapping, snapshot: CustodySessionSnapshot) -> dict:
    return {
        'snapshot_id': row.get('snapshot_id'),
        'provider_session_id': row.get('provider_session_id'),
        'chamber_id': row.get('chamber_id'),
        'session_label': row.get('session_label'),
        'status': row.get('status'),
        'counts': dict(snapshot.counts),
        'observed_at': row.get('observed_at'),
        'reported_at': row.get('reported_at'),
        # **서버가 판정한다.** 프론트가 상태 토큰으로 다시 계산하면 규칙이 두 언어로
        # 쪼개지고, 갈라진 쪽이 화면이면 게이트가 막는 세션이 초록으로 보인다.
        'is_blocking': snapshot.is_blocking,
        'roots': list(row.get('roots') or []),
    }


def _optional_text(value) -> Optional[str]:
    text = str(value or '').strip()
    return text or None
