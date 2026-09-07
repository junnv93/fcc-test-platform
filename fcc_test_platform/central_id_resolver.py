"""Concrete in-memory ``CentralIdResolverPort`` (FE-P0c Phase B).

The Port contract (``CentralIdResolverPort``) and its loud-fail error
(``CentralIdResolutionError``) moved to ``domain/ports/output/central_id_resolver_port``
in FE-P0c WIRE (2026-05-26) — the hexagonal-correct home for a driven-port
abstraction, alongside its sibling ``central_backend_sync_port``. They are
re-exported here so existing callers
(``from fcc_test_platform.application.headless.central_id_resolver import CentralIdResolverPort``)
keep working unchanged.

This module owns the *concrete* in-memory resolver used by fakes/tests. The
production resolver (deterministic uuid5 + ``projects`` lookup) lives in
``application/headless/postgres_central_id_resolver``.
"""
from __future__ import annotations

from typing import Optional

from fcc_test_platform.domain.ports.output.central_id_resolver_port import (
    CentralIdResolutionError,
    CentralIdResolverPort,
    ModelProjectResolution,
)


__all__ = [
    'CentralIdResolutionError',
    'CentralIdResolverPort',
    'InMemoryCentralIdResolver',
    'ModelProjectResolution',
]


#: 이 대역의 세션 **등록부** 키. 한 dict 에 세 모양이 산다:
#:
#:     int                            chamber 도 target 도 없는 등록
#:     (chamber, local_id)            chamber 스코프
#:     (chamber, target, local_id)    chamber+target 스코프
#:
#: ── 판정 (2026-09-07, intent/fake-resolver-honours-target-scope) ─────────────
#:
#: 앞선 커밋이 「셋째 모양은 읽기만 하고 쓰는 곳이 없다」를 남기며 **동작 변경은 별도
#: 판정**이라고 적었다. 그 판정이 여기 있다.
#:
#: ⚠️ **실물의 3-튜플을 «키 모양»으로 베끼는 것이 아니다.** 실물
#:    (``PostgresCentralIdResolver._session_cache``)의 그 dict 는 순수 함수
#:    ``uuid5(ns, session_uuid_name(...))`` 의 **메모이제이션**이고, 실측상 miss 경로와
#:    hit 경로가 같은 값을 낸다 — 통째로 지워도 관측 가능한 동작이 같은 «캐시»다.
#:    이 클래스의 dict 는 캐시가 아니라 **등록부**다. 값을 계산하지 않고 돌려준다.
#:    같은 모양의 dict 지만 역할의 종이 다르고, 캐시 키 규약을 등록부에 이식할 이유는
#:    없다.
#:
#: ⚠️ **그러나 그 키가 인코딩하는 «target 스코프»는 최적화가 아니라 포트가 선언한
#:    계약이다** — ``CentralIdResolverPort.resolve_session_uuid`` 의 docstring:
#:    "``target_identity`` scopes the uuid to one measurement target. It is required
#:    for the same reason ``chamber_id`` is." 그러므로 이 대역도 그것을 지켜야 한다.
#:    지키게 하는 방법이 「실물을 복사」가 아니라 「등록부의 이디엄으로 표현」인 것뿐이다.
#:
#: 그래서 ``register_session`` 이 셋째 모양도 **쓴다**(아래). reader 가 읽는 모양을
#: writer 가 못 만드는 비대칭 자체가 다음 사람이 밟을 자리였다 — 그 등호를
#: ``tests/test_central_id_resolver_target_scope.py`` 가 **AST 로 파생해** 봉인한다.
#: 상수로 적지 않은 이유: 네 번째 모양이 생긴 날 조용해지기 때문이다.
_SessionKey = int | tuple[str, int] | tuple[str, str, int]


class InMemoryCentralIdResolver:
    """Concrete in-memory resolver — used by fakes/tests + ingestion worker
    composition root before a Postgres lookup adapter is introduced.

    Both mappings are populated by the composition root from the local DB
    (``test_sessions.id`` / ``test_sessions.project_id``) and the central
    discovery query (``SELECT id FROM projects WHERE project_code = ?``).
    """

    def __init__(
        self,
        *,
        session_uuid_by_local_id: Optional[dict['_SessionKey', str]] = None,
        project_uuid_by_code: Optional[dict[str, str]] = None,
        project_uuid_by_model_number: Optional[dict[str, str]] = None,
        ambiguous_model_numbers: Optional[frozenset] = None,
    ) -> None:
        self._session_uuid = dict(session_uuid_by_local_id or {})
        self._project_uuid = dict(project_uuid_by_code or {})
        # Model → project is a *separate* mapping from code → project: a model
        # number is not a project code, and conflating them would let a test
        # pass with the wrong key shape.
        self._project_uuid_by_model = dict(project_uuid_by_model_number or {})
        self._ambiguous_models = frozenset(ambiguous_model_numbers or ())

    def register_session(
        self,
        local_session_id: int,
        central_session_uuid: str,
        *,
        chamber_id: str = '',
        target_identity: str = '',
    ) -> None:
        """Record one pre-assigned mapping, at the narrowest scope given.

        ``target_identity`` writes the same three-part key
        ``resolve_session_uuid`` reads. Without it this writer could only make
        two of the reader's three shapes, and the third was reachable **only**
        through constructor injection — an asymmetry that silently drops the
        target scope the port declares.

        ⚠️ The chamber is spelled ``str(chamber_id or '')`` here to match the
        reader **exactly**. This class does not apply
        ``normalize_chamber_id`` (the production resolver does, mapping an
        absent chamber to ``LEGACY_CHAMBER_ID``). That is a second, separate
        divergence: measured, named, and deliberately left alone by this
        change — moving two axes at once would make the result unattributable.
        """
        if not central_session_uuid:
            raise CentralIdResolutionError(
                f'central_session_uuid is required (local={local_session_id})'
            )
        key = int(local_session_id)
        target = str(target_identity or '').strip()
        if target:
            self._session_uuid[(str(chamber_id or ''), target, key)] = str(
                central_session_uuid
            )
        elif chamber_id:
            self._session_uuid[(str(chamber_id), key)] = str(central_session_uuid)
        else:
            self._session_uuid[key] = str(central_session_uuid)

    def register_project(self, local_project_code: str, central_project_uuid: str) -> None:
        if not local_project_code or not central_project_uuid:
            raise CentralIdResolutionError(
                f'project mapping requires non-empty code+uuid '
                f'(code={local_project_code!r}, uuid={central_project_uuid!r})'
            )
        self._project_uuid[str(local_project_code)] = str(central_project_uuid)

    def resolve_session_uuid(
        self,
        local_session_id: int,
        *,
        chamber_id: Optional[str] = None,
        target_identity: Optional[str] = None,
    ) -> str:
        # This resolver serves pre-registered mappings (tests and the local
        # in-memory path); the target scope is part of the lookup key only when
        # a caller registered one, so existing registrations keep resolving.
        #
        # ⚠️ The ``try`` blocks below are deliberately narrow, wrapping only the
        # operations that can actually raise. ``CentralIdResolutionError``
        # subclasses ``ValueError``, so a wide ``except (KeyError, TypeError,
        # ValueError)`` around the whole body would swallow this method's *own*
        # deliberate raise and re-raise it with the generic message — the guard
        # would still fire and the test would still pass, but the diagnosis
        # would be gone. Green, with nothing behind it.
        target = str(target_identity or '').strip()
        try:
            key = int(local_session_id)
        except (TypeError, ValueError) as exc:
            raise CentralIdResolutionError(
                self._unresolved_session_message(
                    chamber_id, target_identity, local_session_id
                )
            ) from exc

        if target:
            scoped = self._session_uuid.get((str(chamber_id or ''), target, key))
            if scoped is not None:
                return scoped
            # 「선언하지 않은 무관심」과 「선언한 스코프를 무시함」은 다른 명제다.
            # 등록부가 이 (chamber, local_id) 에 대해 target 을 **말한 적이 있으면**,
            # 물은 target 이 그중에 없을 때 target-무관 항목으로 미끄러지는 것은
            # 포트가 금지하는 충돌을 조용히 되살리는 일이다. 말한 적이 없으면
            # ``{11: 'AAA'}`` 는 「스코프 무관하게 11은 AAA」라는 caller 의 **선언**
            # 이고, 그것을 깨뜨리는 것은 결함 수리가 아니라 오탐이다. 오탐을 내는
            # 게이트는 우회를 가르친다.
            if self._has_target_scoped_registration(chamber_id, key):
                raise CentralIdResolutionError(
                    f'target_identity={target_identity!r} is not registered for '
                    f'chamber_id={chamber_id!r} local session_id={local_session_id!r}, '
                    f'and this registry declares target scope for that session — '
                    f'refusing to fall back to a target-agnostic mapping'
                )

        try:
            if chamber_id:
                return self._session_uuid[(str(chamber_id), key)]
            return self._session_uuid[key]
        except KeyError as exc:
            raise CentralIdResolutionError(
                self._unresolved_session_message(
                    chamber_id, target_identity, local_session_id
                )
            ) from exc

    @staticmethod
    def _unresolved_session_message(
        chamber_id: Optional[str],
        target_identity: Optional[str],
        local_session_id: object,
    ) -> str:
        """The one wording for "nothing is registered here".

        Two call sites raise it — a non-integer local id and a genuine miss.
        A second copy of the f-string would drift the day one of them grows a
        field, and the drift would be invisible: both still raise, both still
        say something plausible.
        """
        return (
            f'no central session uuid registered for chamber_id={chamber_id!r} '
            f'target_identity={target_identity!r} '
            f'local session_id={local_session_id!r}'
        )

    def _has_target_scoped_registration(
        self, chamber_id: Optional[str], local_session_id: int
    ) -> bool:
        """Does the registry name a target for this ``(chamber, local_id)``?

        Scans the registry rather than keeping an index beside it. A second
        structure would be a second thing to keep in step, and this registry is
        a test double holding a handful of rows — the scan *is* the derivation.
        """
        chamber = str(chamber_id or '')
        return any(
            isinstance(registered, tuple)
            and len(registered) == 3
            and registered[0] == chamber
            and registered[2] == local_session_id
            for registered in self._session_uuid
        )

    def resolve_project_uuid(self, local_project_id: Optional[str]) -> Optional[str]:
        if local_project_id is None or local_project_id == '':
            return None
        try:
            return self._project_uuid[str(local_project_id)]
        except KeyError as exc:
            raise CentralIdResolutionError(
                f'no central project uuid registered for project_code={local_project_id!r}'
            ) from exc

    def resolve_project_by_model_number(
        self, model_number: Optional[str]
    ) -> ModelProjectResolution:
        token = str(model_number or '').strip()
        if not token:
            return ModelProjectResolution(reason='model_number is empty')
        if token in self._ambiguous_models:
            return ModelProjectResolution(
                reason=f'model_number={token!r} maps to more than one central project'
            )
        resolved = self._project_uuid_by_model.get(token)
        if not resolved:
            return ModelProjectResolution(
                reason=f'model_number={token!r} is not registered centrally'
            )
        return ModelProjectResolution(project_uuid=str(resolved))
