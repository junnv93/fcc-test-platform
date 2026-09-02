"""Output ports — 플롯 원본 보관 판정의 중앙 수신 + 조회 (plot-dual-custody ①, 2026-08-09).

**중앙은 판정하지 않는다.** 플롯 원본은 회사 파일서버나 챔버 PC 로컬에 있고 중앙은
그 어느 쪽도 열 수 없다. 그래서 이 경계를 지나는 것은 "판정해 달라"가 아니라 **이미
내려진 판정**이다. 참조 데이터가 중앙→로컬 PULL 인 것의 거울상이고, 방향이 반대인
이유는 하나다 — **증거가 있는 쪽이 판정한다.**

Two narrow ports (``central_test_equipment_list_port`` 의 read/write 분리 미러):

- ``CentralArtifactCustodyWritePort`` — 노드가 보고한 스냅샷을 저장한다.
- ``CentralArtifactCustodyReadPort`` — 프로젝트/세션 축으로 읽는다.

**write 계약의 핵심은 latest-wins 가 반환값에 드러난다는 것이다.** ``store_report``
는 저장된 세션과 **밀려난**(superseded) 세션을 나눠 돌려준다. 밀려남을 조용히 성공으로
접으면 노드는 "보냈고 저장됐다"고 믿는데 중앙은 옛 판정을 그대로 갖고 있고, 그 불일치는
아무 데도 드러나지 않는다. 밀려나는 것은 **정상**이다(재시도가 순서를 뒤집을 수 있다) —
정상이지만 조용해서는 안 된다.

예외 2분류(각각 404 / 503 으로 매핑된다):

- ``ArtifactCustodyNotFoundError`` — 없는 스냅샷, 또는 **다른 프로젝트의 스냅샷**
  (존재를 알려주지 않는다 — 알려주면 한 프로젝트의 뷰어가 id 하나로 남의 프로젝트에
  보관 문제가 있는지 여부를 알아낸다).
- ``CentralArtifactCustodyError`` — 인프라 실패. **빈 결과로 강등하지 않는다.**
  보관 조회에서 빈 결과는 "이상 없음"처럼 읽히는데, 그것이 백엔드 장애를 의미한다면
  이 축이 존재하는 이유(안 옮긴 걸 알려준다)가 정확히 뒤집힌다.

dependency-free: stdlib typing 만 (infrastructure / psycopg / fastapi /
sqlalchemy / pandas / openpyxl / PySide6 import 0).
"""
from __future__ import annotations

from typing import Mapping, Optional, Protocol, Sequence, runtime_checkable


__all__ = [
    'CentralArtifactCustodyError',
    'ArtifactCustodyNotFoundError',
    'ArtifactCustodyProviderNotFoundError',
    'CentralArtifactCustodyWritePort',
    'CentralArtifactCustodyReadPort',
]


class CentralArtifactCustodyError(RuntimeError):
    """중앙 보관 원장 접근 실패 (인프라). 빈 결과로 강등하지 않는다."""


class ArtifactCustodyNotFoundError(CentralArtifactCustodyError):
    """스냅샷이 없거나 이 프로젝트의 것이 아니다."""


class ArtifactCustodyProviderNotFoundError(CentralArtifactCustodyError):
    """보고에 실린 ``provider_id`` 가 중앙 registry 에 없다 (2026-09-03).

    ⚠️ **이것을 503 으로 내보내면 안 된다.** provider 는 운영자가 등록하는 참조
    데이터이지 인입되는 것이 아니므로, 없는 provider 로 보고가 오는 것은 **클라이언트
    잘못**이다. 서비스 docstring 의 Platform Boundary Honesty 가 ``status`` 어휘에
    대해 말하는 것과 같은 규율이고, 형제 ``ReferenceProviderNotFoundError`` 가 같은
    이유로 404 다.

    이 구분이 필요한 이유가 실측으로 있다(2026-09-03): 자연키를 uuid 컬럼에 그대로
    넣던 동안 이 축은 ``503 invalid input syntax for type uuid`` 를 냈고, 그것은
    **중앙 장애처럼 읽혔다.** 운영자는 컨테이너를 보러 갔지 보낸 값을 보러 가지
    않는다. 해소를 붙이면서 그 오독의 자리도 함께 없앤다.
    """


@runtime_checkable
class CentralArtifactCustodyWritePort(Protocol):
    """노드가 보고한 보관 스냅샷을 저장한다."""

    def store_report(
        self,
        *,
        provider_id: str,
        chamber_id: str,
        sessions: Sequence[Mapping],
    ) -> dict:
        """스냅샷들을 latest-wins 로 저장한다.

        Returns:
            ``{'accepted': [provider_session_id, ...],
               'superseded': [provider_session_id, ...]}``

            ``superseded`` 는 **중앙이 이미 더 새로운 관측을 갖고 있어** 거절된
            세션이다. 실패가 아니라 순서 뒤집힘이고, 그래서 예외가 아니라 목록이다.
        """
        ...


@runtime_checkable
class CentralArtifactCustodyReadPort(Protocol):
    """프로젝트/세션 축 보관 현황 조회."""

    def list_project_snapshots(self, project_id: str) -> list[dict]:
        """이 프로젝트에 귀속된 세션들의 스냅샷 행.

        귀속은 ``test_sessions`` 자연키 조인으로 정해진다 — 스냅샷이 세션 FK 를
        들지 않는 이유가 여기 있다(조인키가 나중에 해소돼도 백필이 필요 없다).
        """
        ...

    def count_unresolved_snapshots(self, project_id: str) -> int:
        """이 프로젝트와 **관련 있는** provider 중 아직 귀속되지 않은 스냅샷 수.

        **0 이 아닌 값을 화면이 말해야 한다.** 조용히 빼면 "이 프로젝트는 이상 없음"이
        "이 프로젝트에서 우리가 볼 수 있는 것은 이상 없음"과 구분되지 않는다.

        ⚠️ **전역 개수를 돌려주면 안 된다.** 이 값은 프로젝트 화면에 프로젝트 숫자로
        렌더되므로, 다른 provider/챔버의 미귀속 스냅샷까지 세면 (a) 한 프로젝트 뷰어가
        볼 권한 없는 fleet 전체 상태를 알게 되고 (b) 시험원이 **자기가 해소할 수 없는**
        건에 대해 "모델을 등록하라"는 안내를 영구히 보게 되어, 이 필드가 지키려던 구분이
        정확히 무너진다(독립 리뷰 2026-08-09). 범위는 이 프로젝트에 세션을 보낸
        provider 들이다 — 그들만이 이 프로젝트에 귀속**될 수 있는** 스냅샷을 만든다.
        """
        ...

    def count_sessions_without_snapshot(self, project_id: str) -> int:
        """Count project sessions that have no custody snapshot at all.

        This is intentionally separate from ``count_unresolved_snapshots``:
        unresolved means a snapshot exists but cannot be attributed, while
        this count means the project session has no reported snapshot.
        """
        ...

    def get_snapshot(self, project_id: str, snapshot_id: str) -> dict:
        """한 스냅샷의 상세 + findings.

        Raises:
            ArtifactCustodyNotFoundError: 없거나 이 프로젝트의 것이 아닐 때.
        """
        ...
