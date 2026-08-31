"""Session API contracts SSOT.

Dependency-free. 외부 컨슈머(SDK, OpenAPI 추출기, AuthZ 정책)가 라우터/어댑터를
import하지 않고도 권한 카탈로그/route/version/DTO를 알 수 있게 한다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from domain.models.session_event import SessionEvent, SessionEventKind


__all__ = [
    'SESSION_API_VERSION',
    'SESSION_API_MULTIPART_OPERATIONS',
    'SESSION_API_OPERATIONS',
    'SESSION_API_PERMISSIONS',
    'SESSION_API_ROUTES',
    'SESSION_PERMISSION_READ',
    'SESSION_PERMISSION_CONTROL',
    'SESSION_PERMISSION_EVENTS',
    'SessionEventView',
    'SessionInfoResponse',
    'SessionOperationResponse',
    'WorkbookUploadResponse',
]


SESSION_API_VERSION = '2'

SESSION_PERMISSION_READ = 'session:read'
SESSION_PERMISSION_CONTROL = 'session:control'
SESSION_PERMISSION_EVENTS = 'session:events'


SESSION_API_PERMISSIONS: dict[str, str] = {
    'start_session': SESSION_PERMISSION_CONTROL,
    'stop_session': SESSION_PERMISSION_CONTROL,
    'upload_session_workbook': SESSION_PERMISSION_CONTROL,
    'get_session_progress': SESSION_PERMISSION_READ,
    'get_session_running': SESSION_PERMISSION_READ,
    'get_session_info': SESSION_PERMISSION_READ,
    'subscribe_session_events': SESSION_PERMISSION_EVENTS,
}


SESSION_API_ROUTES: dict[str, tuple[str, str]] = {
    'start_session': ('POST', '/session/start'),
    'stop_session': ('POST', '/session/stop'),
    'upload_session_workbook': ('POST', '/session/workbook'),
    'get_session_progress': ('GET', '/session/progress'),
    'get_session_running': ('GET', '/session/is-running'),
    'get_session_info': ('GET', '/session/info'),
    'subscribe_session_events': ('WEBSOCKET', '/session/events'),
}


#: Operations whose request body is ``multipart/form-data`` rather than JSON.
#:
#: A set rather than a property of each route entry because two parties have to
#: give the same answer to one question ("is this POST's body JSON?"): the
#: OpenAPI builder and the schema invariant that requires every POST to declare
#: a typed body. Before this existed, that invariant reached straight into
#: ``content['application/json']`` and would have raised ``KeyError`` on the
#: first multipart operation — the assertion was not wrong about POSTs needing a
#: body, only about there being one way to spell one.
#:
#: ⚠️ The route registration does **not** read this set — the session router
#: writes its handlers by hand, so a multipart operation's ``async def`` is a
#: property of the handler, not something derived from here. An earlier draft of
#: this comment claimed otherwise, which would have left a second multipart
#: operation with a silently synchronous handler (FastAPI cannot inject
#: ``UploadFile`` into one). ``TestEveryMultipartOperationHasAnAsyncHandler``
#: checks the actual routes instead, so the obligation is enforced where it
#: really lives.
SESSION_API_MULTIPART_OPERATIONS: frozenset[str] = frozenset({
    'upload_session_workbook',
})


SESSION_API_OPERATIONS: dict[str, dict[str, Any]] = {
    name: {
        'permission': SESSION_API_PERMISSIONS[name],
        'method': SESSION_API_ROUTES[name][0],
        'path': SESSION_API_ROUTES[name][1],
    }
    for name in SESSION_API_PERMISSIONS
}


@dataclass(frozen=True)
class SessionEventView:
    """Wire-shape for a single session event."""

    kind: str
    payload: tuple[Any, ...] = field(default_factory=tuple)

    @classmethod
    def from_event(cls, event: SessionEvent) -> 'SessionEventView':
        kind = event.kind.value if isinstance(event.kind, SessionEventKind) else str(event.kind)
        return cls(kind=kind, payload=tuple(event.payload))

    def to_response(self) -> dict[str, Any]:
        return {'kind': self.kind, 'payload': list(self.payload)}


@dataclass(frozen=True)
class SessionInfoResponse:
    """Static metadata about the session API surface."""

    api_version: str = SESSION_API_VERSION
    operations: tuple[str, ...] = tuple(SESSION_API_OPERATIONS.keys())
    routes: tuple[tuple[str, str, str], ...] = tuple(
        (name, method, path)
        for name, (method, path) in SESSION_API_ROUTES.items()
    )

    def to_response(self) -> dict[str, Any]:
        return {
            'api_version': self.api_version,
            'operations': list(self.operations),
            'routes': [
                {'operation': op, 'method': method, 'path': path}
                for op, method, path in self.routes
            ],
        }


@dataclass(frozen=True)
class WorkbookUploadResponse:
    """Wire-shape for a stored workbook upload.

    ``workbook_id`` is an **opaque** handle: the only thing a client may do with
    it is hand it back to ``start_session``. It happens to be derived from the
    content digest, and that fact is deliberately not exposed as a ``sha256``
    field — a client able to compute handles locally could name a workbook it
    never uploaded, which is precisely the arbitrary-path parameter this
    operation exists to avoid.

    ``filename`` is the client's own name, normalized and echoed back so a human
    can recognise what they sent. Nothing on disk is named from it.
    """

    workbook_id: str
    filename: str
    size_bytes: int

    def to_response(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SessionOperationResponse:
    """Wire-shape for start/stop responses."""

    operation: str
    api_version: str
    progress: dict[str, Any]

    def to_response(self) -> dict[str, Any]:
        return asdict(self)


def iter_session_operation_permissions() -> Iterable[tuple[str, str]]:
    """Operation → permission mapping iterator (test/audit convenience)."""
    return SESSION_API_PERMISSIONS.items()
