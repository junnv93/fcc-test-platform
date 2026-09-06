"""Chamber progress broadcast outbound port (멀티챔버 P7/B4, 2026-06-18).

중앙 진행 릴레이(ADR-0015 Option B — 노드 push 릴레이)의 fan-out 경계 Port. 중앙
Platform API 의 heartbeat ingest 경로가 in_use progress 를 받으면 이 Port 로
:class:`ChamberProgressEvent` 를 publish 하고, 중앙 WebSocket fan-out 엔드포인트가
:meth:`subscribe` 로 그것을 구독해 웹 브라우저에게 재발행한다.

Port 는 그 버스의 **전 표면**(publish · subscribe · dispose)을 정의한다.

## 왜 이렇게 바뀌었나 — 옛 「publish-only」 선언은 거짓 전제 위에 있었다 (2026-09-07)

2026-06-18 부터 이 파일은 스스로를 「publish-only」라 선언하면서 그 근거로 이렇게
적었다: *"subscribe 는 WS driving adapter 가 인프라 broadcaster 에 **직접 접근**"*.
**그 문장은 태어난 날부터 거짓이었다.** 실측:

- 이 릴레이를 만든 커밋(모노레포 ``0f2aaeef``, 2026-06-18)의 WS 핸들러도
  ``broadcaster = adapter.progress_broadcaster`` 로 받았다 — 인프라 객체가 아니라
  **이 Port 타입으로 선언된 프로퍼티**를 통해서다. 직접 접근한 적이 없다.
- 오늘 이 레포에서는 그것이 가능하지도 않다: ``fcc_test_platform.api`` 층은
  ``infrastructure`` 에서 이름을 **0개** import 한다(AST 실측).

그래서 「좁은 의존이 도메인 경계를 publish-only 로 유지한다」는 결론도 성립한 적이
없다. 넓히는 판정의 나머지 근거(실측):

- ⚠️ **ADR-0015 는 이 Port 의 모양을 결정하지 않는다.** 그 ADR 172줄에서
  ``publish``/``subscribe``/``port`` 는 2회 등장하고 둘 다 *노드의 기존* 이벤트
  버스를 서술한다. 그 ADR 이 정한 것은 전송 **방향**(노드 push vs 중앙 pull)이다.
  즉 「publish-only」의 근거로 남아 있던 것은 위의 거짓 문장 하나뿐이었다.
- 이 레인의 규칙은 「능력 하나에 포트 하나」가 **아니다**. 28개 포트 모듈 실측:
  Read/Write 로 갈린 쌍은 전부 *구현 클래스가 둘*인 경우다(reference·project·
  equipment·chamber·report·sample-inventory). 한 객체가 받치는 Port 는 이질적
  능력을 함께 담는다 — ``PlatformIngestionTransaction``(upsert+commit+rollback) ·
  ``CentralResultSelectionPort``(list+append) · ``CentralChamberWritePort`` 는
  이름이 Write 인데 ``read_*`` 를 둘 담는다. 진행 버스는 **한 객체**다.
- 이 Port 의 구현(:class:`ChamberProgressBroadcaster`)은 자기 docstring 에서
  노드의 ``InMemoryEventBus`` 를 「복제」한다고 적는다. 그 버스의 Port 인
  ``SessionEventPort`` 는 publish + subscribe + dispose 셋을 함께 선언한다.
  구조가 같은 두 버스가 반대 모양의 Port 를 가질 이유는 위의 거짓 문장뿐이었다.

⚠️ **읽기 능력을 별도 Port 로 가르지 않은 이유**도 위 세 번째 항목이다. 가르면
조립 루트가 같은 객체를 두 인자로 넘기게 되는데, 이 레인에 그런 선례가 없다 —
갈린 Port 쌍은 언제나 구현도 갈려 있다.

dependency-free — ``infrastructure``/``pyvisa``/``openpyxl``/``pandas``/``PySide6``
import 0 (``TestDomainPurity``). ``AsyncIterator`` 는 stdlib ``typing`` 이다.
"""
from __future__ import annotations

from types import TracebackType
from typing import AsyncIterator, Protocol, runtime_checkable

from fcc_test_kernel.domain.models.chamber_node import ChamberProgressEvent


__all__ = ['ChamberProgressBroadcastPort', 'ChamberProgressSubscription']


class ChamberProgressSubscription(Protocol):
    """:meth:`ChamberProgressBroadcastPort.subscribe` 가 돌려주는 것.

    ⚠️ **이것을 ``AsyncIterator`` 로만 적으면 안 된다.** WS 릴레이가 쓰는 철자는

        async with bus.subscribe() as subscription:
            async for event in subscription:

    이고, 그 ``async with`` 없이는 끊긴 구독이 버스의 strong-ref 집합에 남아 이후의
    모든 publish 가 죽은 큐를 채운다. 즉 **컨텍스트 매니저 표면이 계약의 일부**다.
    구독 해제를 「구현이 알아서」에 맡기지 않고 여기에 적는 이유가 그것이다.

    (노드의 ``SessionEventPort`` 는 이 요구를 ``subscribe`` docstring 의 *산문*으로만
    적는다 — *"반환된 객체는 ``async with`` 컨텍스트 매니저로도 사용 가능해야 한다"*.
    산문은 검사되지 않는다. 여기서는 타입으로 적는다.)
    """

    async def __aenter__(self) -> AsyncIterator[ChamberProgressEvent]: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


@runtime_checkable
class ChamberProgressBroadcastPort(Protocol):
    """진행 이벤트를 구독자에게 fan-out 하는 outbound port (publish · subscribe · dispose).

    ⚠️ ``runtime_checkable`` 이라 ``isinstance`` 가 통하지만, 그것은 메서드의
    **존재**만 본다 — 반환 타입은 보지 않는다. 그래서 구현은 이 Protocol 을
    **명시 상속**해 mypy 가 전 표면을 검사하게 한다(이 레인의 선례:
    ``PostgresCentralProjectReferenceAdapter`` 외 4). 옛 결함이 정확히 반환 타입
    좁힘이었으므로 ``isinstance`` 축만으로는 재발을 못 막는다.
    """

    def publish(self, event: ChamberProgressEvent) -> None:
        """진행 이벤트를 모든 활성 구독자에게 fan-out.

        구독자가 없으면 조용히 drop(합법적 — 진행 이벤트는 best-effort 릴레이이며
        권위 SSOT 는 C1 heartbeat ledger 다). 구현은 thread-safe 해야 한다(측정
        스레드/요청 스레드 어디서든 호출 가능).
        """
        ...

    def subscribe(self) -> ChamberProgressSubscription:
        """새 구독 채널을 연다 — ``async with`` 로 감싸고 ``async for`` 로 소비한다.

        스코프를 벗어나면 구독은 **반드시** 해제된다
        (:class:`ChamberProgressSubscription` 의 ⚠️ 를 보라).
        """
        ...

    def dispose(self) -> None:
        """모든 구독 채널을 닫고 이후의 publish 를 no-op 으로 만든다.

        조립 루트(``PlatformApiRuntime.dispose``)가 재구성 전에 부른다 — 안 부르면
        재구성된 런타임이 옛 구독을 물고 시작한다.
        """
        ...
