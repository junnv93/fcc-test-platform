"""platform 도메인 모델 재수출.

⚠️ `TestPlanSnapshot` 과 알림 타입 셋은 이 배포판을 떠났다 — 2026-09-07 에
`fcc_test_kernel` 로 상류 수리됐다(kernel-v0.5.2 · v0.5.3). 이 배포판 «안»에서
그것들을 쓰는 코드가 없었기 때문이다(실소비자는 전부 모노레포 GUI 폐포).

여기서 재수출하지 않는다. 재수출은 「이 배포판이 그 이름의 출처」라고 말하는데,
그것이 더 이상 참이 아니다. 부르는 쪽이 커널을 직접 보게 한다 — 사본이 하나
남으면 언젠가 갈라지고, 갈라져도 양쪽 검사가 초록이다.

    TestPlanSnapshot                     → fcc_test_kernel.domain.models.test_plan_snapshot
    NotificationLevel/EventTypes/Event   → fcc_test_kernel.domain.models.notification_types
"""

__all__: list[str] = []
