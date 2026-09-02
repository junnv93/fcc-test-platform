"""OpenAPI 3.1 schema SSOT for the Platform read API surface (FE-P0d, 2026-05-27).

Dependency-free. Derives an OpenAPI 3.1 document from ``PLATFORM_API_ROUTES`` +
``PLATFORM_API_OPERATIONS`` + ``PLATFORM_API_SCHEMAS`` + ``PLATFORM_API_PATH_PARAMS``
+ ``PLATFORM_API_PERMISSION_DESCRIPTIONS`` (all ``platform.api_contracts`` SSOT).

Mirrors ``application.headless.api_schema`` and reuses the shared OpenAPI 3.1
normalization helpers (``application.common.openapi_schema_builder``) so the
two surfaces cannot drift on ``$ref`` / ``nullable`` handling. Per-surface info
block + summaries live here.

The artifact (``docs/api/platform-api.openapi.json``) MUST be byte-identical to
``build_platform_openapi_schema(config)`` (CI drift gate via
``scripts/export_session_api_schemas.py --verify`` +
``tests/test_platform_read_api_fe_p0d.py``).
"""
from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

from fcc_test_contracts.common.api_error_codes import ApiSurface, surface_error_codes
from fcc_test_contracts.common.openapi_schema_builder import (
    apply_operation_error_responses,
    build_components_schemas,
    iter_path_param_names,
    problem_details_component_schemas,
    problem_error_response,
)
from fcc_test_contracts.common.ws_subprotocol_auth import WS_BEARER_SUBPROTOCOL
from application.central_contract.api_contracts import (
    PLATFORM_API_COMPATIBILITY_MAJOR,
    PLATFORM_API_CONTRACT_VERSION,
    PLATFORM_API_OPERATIONS,
    PLATFORM_API_OPERATION_QUERY,
    PLATFORM_API_OPERATION_QUERY_OVERRIDES,
    PLATFORM_API_PATH_PARAMS,
    PLATFORM_API_PERMISSIONS,
    PLATFORM_API_PERMISSION_DESCRIPTIONS,
    PLATFORM_API_QUERY_PARAMS,
    PLATFORM_API_RESPONSE_HEADERS,
    PLATFORM_API_ROUTES,
    PLATFORM_API_SCHEMAS,
    PLATFORM_API_TITLE,
)

if TYPE_CHECKING:  # pragma: no cover — type-only import keeps runtime light
    from fcc_test_platform.application.runtime_config import PlatformApiConfig


# PLATFORM_API_TITLE is re-exported (SSOT lives in api_contracts) so existing
# importers of ``application.platform.api_schema.PLATFORM_API_TITLE`` keep working.
__all__ = [
    'PLATFORM_API_TITLE',
    'build_platform_openapi_schema',
    'build_platform_asyncapi_schema',
]


# Per-operation natural-language summary (coverage sealed by
# ``test_operation_summary_covers_every_operation``).
_OPERATION_SUMMARIES: dict[str, str] = {
    'list_projects': (
        'List the project directory (read-open, project-status-visibility) — one '
        'row per project joined to its 1:1 device model (ADR-0017 D1) + a sample '
        'count. Visible to ANY authenticated principal (not membership-scoped) so '
        'a team sees the projects it works on; the optional ?status filter '
        '(active|completed|all) defaults to active (in-progress) projects. '
        '?q searches server-side (case-insensitive substring over management '
        'number / project code / customer); ?limit and ?cursor page by keyset '
        '(opaque token in the X-Next-Cursor response header, never OFFSET). '
        'Omitting q/limit/cursor returns the whole directory unchanged.'
    ),
    'complete_project': (
        'Mark a project completed — the project leaves the default active '
        'directory and appears under ?status=completed. Idempotent: completing an '
        'already-completed project succeeds as a no-op (sets status=completed). A '
        'project-management act gated by platform:admin (membership-admin allowed '
        'via project-scoped authorize). Returns the updated project detail; 404 '
        'when the project is unknown.'
    ),
    'reopen_project': (
        'Reopen a project (sets status=active) — the reverse of complete_project, '
        'same platform:admin gate. Idempotent: reopening an already-active project '
        'is a no-op success. Returns the updated project detail; 404 when the '
        'project is unknown.'
    ),
    'create_project': (
        'Create a project = model (ADR-0017 D1) — inserts one projects row + one '
        'device_models row (project_code == model name). A request whose model '
        'name matches an existing project_code reuses that project (idempotent, '
        'never a duplicate). Gated by the authenticated authorization class (a '
        'global operation, not project-scoped); the creator is auto-granted '
        'project_admin membership (D3).'
    ),
    'update_project': (
        'Partially update a project\'s 성적서 표지 메타 (customer / manufacturer / '
        'management_number / fcc_grantee_code / applicant_name / '
        'applicant_address / eut_description / test_standard) — FCC ID and '
        'applicant are normally confirmed AFTER a project starts, so the cover '
        'metadata must stay editable. A field the request omits is left '
        'unchanged; a field sent as null is cleared. status is NOT editable here '
        '(use complete_project / reopen_project) and neither is model_name / '
        'project_code (the project identity — changing it is a re-key, not an '
        'edit); sending either is a 400. Gated by platform:admin, the same tier '
        'as the completion lifecycle. Returns the updated project detail; 404 '
        'when the project is unknown.'
    ),
    'get_project': (
        'Project detail — model + customer/manufacturer + the sample list. '
        'Read-open (project-status-visibility): visible to ANY authenticated '
        'principal (an internal-tool decision — note the detail includes sample '
        'serial / label / sender-receiver / latest-intake fields). 404 when the '
        'project is unknown.'
    ),
    'get_project_coverage': (
        'Project-wide coverage from the central coverage_by_condition_hash view — '
        'one row per (technology, condition_hash) derived from the latest attempt '
        '(FE-P2 dedup/coverage dashboard source).'
    ),
    'list_project_claims': (
        'Active claims from the central active_claims view — the still-held claim '
        'per (project, condition_hash) for FE-P3 lock/warning UX.'
    ),
    'get_project_sync_status': (
        'Central-data freshness for the project (FE-SYNC) — newest central '
        'measurement timestamp + age + is_stale + condition/active-claim counts. '
        'The UI softens the duplicate-prevention guarantee when stale.'
    ),
    'get_project_progress': (
        'Time-weighted progress rollup for the project (Phase 6) — one row per '
        '(workbench area, progress bucket): planned vs completed minutes and the '
        'completion percent (null when the bucket has no priced time, never a fake '
        '0%/100%). Completion comes from measurement coverage joined on the '
        'provider-scoped key (project, provider, condition_hash) so a conducted '
        'headless never completes a radiated condition; unpriced (no catalog time) '
        'and unbucketable conditions are surfaced as counts, not folded into the '
        'percent. Provider-computed bucket/area are read straight from storage '
        '(the platform derives no taxonomy — ADR-0010).'
    ),
    'list_project_report_sessions': (
        'Reportable measurement sessions for a project (Phase 5-C) — one row per '
        '(node_base_url, node-local submit_session_id) with node routing metadata '
        '(node id/name/base_url), completed-condition count, technologies, and the '
        'latest measured_at/verdict. Derived from the central read model only: '
        'coverage_by_condition_hash + test_sessions.provider_session_id + '
        'test_sessions.provider_id -> providers.base_url. Only entries whose '
        'provider_session_id resolves to a '
        'positive integer (submittable to the node Headless report API) are '
        'exposed; the central session UUID and condition_hash never leave the '
        'server. platform:read gated.'
    ),
    'list_project_result_selections': (
        'List the effective completed result for each exact project/provider/'
        'condition partition. The selected event wins over deterministic latest '
        'recency and the response is keyset paginated.'
    ),
    'list_project_result_attempts': (
        'List completed attempts for one exact provider and condition, ordered by '
        'measured_at DESC NULLS LAST, created_at DESC, id DESC.'
    ),
    'select_project_result': (
        'Append a manual result selection event with an expected-revision CAS; '
        'stale writes return 409 and do not append an event.'
    ),
    'ingest_published_plan_expectation': (
        'Register a published test plan centrally: its conditions become this '
        "project's progress denominator AND the answer to whether central knows "
        'the plan id at all. Idempotent by (project, provider, plan, condition).'
    ),
    'clear_project_result_selection': (
        'Append a clear event for one exact provider-scoped result partition.'
    ),
    'list_project_result_references': (
        'List immutable project result reference revisions and their source '
        'selection/session provenance.'
    ),
    'create_project_result_reference': (
        'Publish a provider-authored opaque reference from the current selected '
        'project/provider/condition source. The request supplies only natural '
        'provider selection intent and a bounded reason; the server resolves '
        'event/attempt/session provenance and the provider owns type/schema/'
        'payload/hash compatibility.'
    ),
    'retire_project_result_reference': (
        'Retire a published project result reference without deleting its history.'
    ),
    'list_providers': (
        'List the registered providers as selectable summaries (WEB-PROVIDER-UI-0) '
        '— id + display name + descriptor version, projected from the platform '
        'registry so apps/web builds a backend-driven picker instead of a '
        'browser-local list. Deterministic: entries are ordered by provider_id; '
        'an empty registry returns []. Read-open under platform:read.'
    ),
    'get_provider_ui_descriptor': (
        'Proxy a provider-owned UI descriptor (WEB-PROVIDER-UI-0) — the platform '
        'serves the descriptor from its registry so apps/web renders test plan / '
        'equipment / reference / correction surfaces schema-first without '
        'importing provider internals.'
    ),
    'acquire_project_claim': (
        'Acquire a measurement claim on the central claim_events ledger '
        '(FE-P3-write) — appends an acquired event iff the (project, '
        'condition_hash) is not already held by another operator (else 409), '
        'enforcing cross-engineer duplicate prevention.'
    ),
    'release_project_claim': (
        'Release (or expire) an open claim — appends a released/expired event '
        'referencing the acquired claim_id (409 when no open claim matches).'
    ),
    'list_project_memberships': (
        'List project memberships from the central project_member_permissions '
        'view — one row per (user_subject, role_key) assignment (FE-P8). '
        'platform:read gated so viewer tokens can see the RBAC roster.'
    ),
    'assign_project_membership': (
        'Assign or update a project membership role (FE-P8). UPSERT on '
        '(project_id, user_subject, role_key). 400 when role_key is unknown to '
        'the rbac_role_grants SSOT; 404 when user_subject is unknown. Every '
        'change appends membership.assigned to audit_events.'
    ),
    'revoke_project_membership': (
        'Revoke a project membership role (FE-P8). 404 when the (user, role) '
        'pair has no current assignment. Every change appends membership.revoked '
        'to audit_events.'
    ),
    'list_chambers': (
        'Chamber availability from the central chamber_availability view '
        '(멀티챔버 P2) — one row per registered chamber with the verbatim latest '
        'heartbeat PLUS the service-derived status (idle/in_use/offline). OFFLINE '
        'is computed against the read service injected clock + per-chamber TTL, '
        'never by the DB view.'
    ),
    'register_chamber': (
        'Register (or refresh) a chamber node in the central chamber_nodes '
        'registry (멀티챔버 P2). UPSERT on chamber_id — re-registering refreshes '
        'name/base_url/ttl. platform:admin gated (operator-provisioned).'
    ),
    'push_chamber_heartbeat': (
        'Append a chamber heartbeat to the central chamber_heartbeat_events '
        'append-only ledger (멀티챔버 P2). Node-scoped platform:chamber token — a '
        'chamber PC self-reports idle/in_use (OFFLINE rejected; it is derived from '
        'heartbeat absence/staleness, never reported).'
    ),
    'push_chamber_result_ingestion': (
        'Accept a versioned result outbox batch from the bound chamber node. The '
        'central application owns parent-first mapping, transaction boundaries, '
        'and idempotent upserts; the chamber never sends SQL, a DSN, or a central '
        'ingestion plan. A receipt acknowledges only event ids accepted by the '
        'central writer; platform:chamber is bound to the path chamber_id.'
    ),
    'start_chamber_measurement': (
        'Start a measurement on a chamber via the central proxy (멀티챔버 P5). The '
        'hub validates the active project/sample pair, captures the immutable sample '
        'snapshot, gates on the chamber being idle (in_use/offline → 409), and '
        'forwards the platform-owned envelope to POST /session/start. The web client '
        'authenticates only at the hub. platform:claim gated (engineer-tier remote '
        'action). 404 unknown chamber/sample; 503 node unreachable.'
    ),
    'get_chamber_measurement_progress': (
        'Poll a chamber measurement progress via the central proxy (멀티챔버 P5) — '
        'the hub looks up the chamber base_url and forwards GET /session/progress '
        'to the node Session API, returning the verbatim node progress. '
        'platform:read gated. 404 unknown chamber; 503 node unreachable.'
    ),
    'subscribe_chamber_progress': (
        'Subscribe to real-time chamber progress over WebSocket (멀티챔버 P7/B4, '
        'ADR-0015 Option B). The hub fans out ChamberProgressEvent frames carried '
        'by node heartbeats (C1) — a real-time complement to the GET '
        '/platform/chambers polling fallback (preserved). platform:read gated.'
    ),
    'list_reports': (
        'List the project’s test reports (성적서 instances), newest first. Each '
        'carries a DERIVED report_number (S-{management_number}-{edition}; null '
        'when the project has no management number). platform:read gated.'
    ),
    'create_report': (
        'Create a test report (성적서) for the project at a given edition. '
        'report_number is derived (not in the body); a duplicate (project_id, '
        'edition) → 409, unknown project → 404. platform:admin gated.'
    ),
    'get_report_citation': (
        'Assemble the report header citation from the project + samples + intakes: '
        'derived report_number/FCC ID, applicant/EUT/standard, and per-sample SN + '
        'latest firmware (BL/AP/CP/CSC/RF CAL/HW Rev). Optional ?edition feeds '
        'report_number; sample_number is the local measurement-DB join key. '
        'platform:read gated.'
    ),
    'list_test_equipment_lists': (
        'List the project’s §6 equipment lists (성적서 TEST AND MEASUREMENT '
        'EQUIPMENT), newest first, each with its item count. EMS owns the '
        'standard list; this surface records what the project actually used. '
        'platform:read gated.'
    ),
    'create_test_equipment_list': (
        'Create a §6 equipment list for a test item (e.g. BT, "DFS, UNII"). The '
        'server owns the status (draft); test_item_key is EMS vocabulary and is '
        'not validated beyond being non-blank. A duplicate natural key → 409, '
        'unknown project → 404. platform:claim gated (engineer tier).'
    ),
    'get_test_equipment_list': (
        'Read one §6 equipment list with its items and the two tables’ column '
        'order. The column order ships in ``tables`` so no consumer (web editor, '
        'DOCX patcher) re-declares it. A list belonging to another project is '
        '404, not 403. platform:read gated.'
    ),
    'replace_test_equipment_list_items': (
        'Replace every item of a draft §6 equipment list in one transaction. '
        'sort_order is the array position and is assigned by the server (the '
        'request body has no sort_order). A confirmed list → 409. platform:claim '
        'gated (engineer tier).'
    ),
    'attach_test_equipment_list': (
        'Attach a draft §6 equipment list to a report edition. A tester can build '
        'the list before the report row exists, so this is how it later reaches an '
        'edition. A confirmed list, or one already attached elsewhere, → 409. '
        'platform:claim gated (engineer tier).'
    ),
    'confirm_test_equipment_list': (
        'Confirm (freeze) a §6 equipment list — the snapshot the report is '
        'rendered from. Already confirmed or empty → 409; an empty §6 table is '
        'refused at report generation anyway. platform:claim gated (engineer tier).'
    ),
    'list_reference_revisions': (
        'List reference-catalog revisions for a provider, narrowed by the '
        'identity facets (family / scope_kind / scope_id / state) and keyset '
        'paginated. The central platform is the authoritative origin of this data; '
        'a chamber PC holds a replica. platform:read gated.'
    ),
    'get_reference_revision': (
        'Read one reference revision with its entries and the column order to '
        'render them in (payload_columns — the provider domain\'s runtime row '
        'field order, so a client never re-declares the six families\' field '
        'lists). This is the review step publication depends on: the listing '
        'returns summaries, and publishing something nobody has seen is not '
        'review. platform:read gated.'
    ),
    'create_reference_revision': (
        'Create a CANDIDATE reference revision from imported or forked entries. '
        'Never publishes: publication is a separate human review step, which is '
        'what keeps "no published revision" — the state of every existing '
        'database — a provable no-op for measurement. platform:reference-write '
        'gated (the tester who re-measures a value is the one who records it).'
    ),
    'fork_reference_revision': (
        'Copy a PUBLISHED revision into a new CANDIDATE the tester can edit, '
        'linked back through forked_from_revision_id. This is what makes the '
        'tester the author: before it existed the only way to produce a '
        'candidate was the operator-run workbook importer, so a tester who '
        're-cabled a chamber and re-measured the loss had to wait for someone '
        'else — and the workbook stayed authoritative for as long as the wait. '
        'Only a published revision may be forked (a candidate is already yours '
        'to edit); anything else is refused 409. Entries are copied verbatim, '
        'so the child\'s content hash equals the parent\'s until something is '
        'actually changed. provenance_kind is INHERITED rather than stamped '
        'FORK_EDIT — a copy nobody edited still holds the parent\'s values. '
        'Returns the full detail so the client can open it without a second '
        'round trip. platform:reference-write gated.'
    ),
    'list_reference_families': (
        'List the reference families a revision can be authored for, with the '
        'exact payload columns each one requires. The from-scratch authoring '
        'screen needs this before any revision exists for a family, and it is '
        'the same rule the revision detail already follows: the column '
        'vocabulary is served, never re-declared in the client.'
    ),
    'create_authored_reference_revision': (
        'Create a reference revision authored on the web, with no workbook behind '
        'it. Separate operation from the workbook importer rather than a relaxed '
        'version of it: which operation ran IS the provenance, so the revision is '
        'stamped WEB_AUTHORED and carries no snapshot link. Every derived value — '
        'reference_id, identity_key and the content hashes — is minted by the '
        'server from the payloads; a client-supplied identity could describe a '
        'different row than the one stored, and that only surfaces when the '
        'projection fills the table the measurement path reads.'
    ),
    'update_reference_revision_rows': (
        'Add and remove ROWS of a candidate in one transaction. This is the '
        'operation the value-edit policy refuses to perform, and it refuses on '
        "purpose: changing an identity field is an add plus a remove, not an "
        'edit, so folding the two together would make a typo in an identity cell '
        'and an intended row replacement look like the same request. Removals '
        'run before additions so a request that replaces a row with a new one '
        'carrying the same identity does not collide with the unique index. '
        'entry_order continues past the current maximum and gaps are left behind: '
        'the index wants uniqueness, not density.'
    ),
    'update_reference_revision_entries': (
        'Change values in named rows of a CANDIDATE. Only the rows listed in '
        'edits travel, and only their payloads — a correction curve carries '
        'thousands of points, and re-sending all of them to change one would '
        'be both wasteful and a lost-update channel, because the untouched '
        'points in the resend would overwrite whatever someone else changed. '
        'expected_etag is the concurrency token and it is checked inside the '
        'UPDATE\'s WHERE clause, so there is no window in which two edits both '
        'believe they won; a stale one is refused 409. Three edits are refused '
        '400 rather than partially applied: an unknown reference_id (a skipped '
        'edit reports success while the value never changes), a payload whose '
        'key set is not the family runtime row (the payload IS the row the '
        'measurement path reads), and any change to an identity field (that is '
        'an add plus a remove, and it would orphan the stored identity_key). '
        'Derived values are recomputed by the server and appear in no request '
        'field. A real change promotes provenance_kind to FORK_EDIT; '
        're-submitting identical values writes nothing, so the token keeps '
        'meaning "a person\'s number is in here". platform:reference-write gated.'
    ),
    'publish_reference_revision': (
        'Publish a candidate revision, making its family catalog-owned for that '
        'scope. At most one published revision per (provider, family, profile, '
        'scope) is enforced by a partial unique index in the central DDL, so a '
        'second publish is refused at the origin rather than detected later on a '
        'chamber replica. A COUPLED family group (correction ↔ switch port '
        'mapping) must name its sibling candidate in coupled_revision_id and '
        'both move in one transaction: publishing one half pairs one antenna\'s '
        'signal path with another\'s path loss, and the measurement completes, '
        'yields a verdict, and is silently wrong. platform:reference-write gated.'
    ),
    'get_chamber_reference_bundle': (
        'Fetch the published reference data a chamber must measure with: the '
        'room-scoped families for that chamber plus, when ?scope_project_id is '
        'supplied, the project-scoped ones. Node-driven pull, so an unreachable '
        'central degrades the chamber to a stale replica rather than stopping it, '
        'and the staleness is observable on the node where the question is asked. '
        'Every page of one bundle repeats the same bundle_etag; echo it back as '
        '?bundle_etag= to get an "unchanged" answer with no rows. '
        'platform:chamber gated and bound to the path chamber_id.'
    ),
    'update_chamber_storage_root': (
        'Set (or clear) where a chamber PC writes its measurement plots. This is '
        'a property of the machine, not of a session: every test run in that room '
        'should land in the same place, and the workbook cell that used to decide '
        'it is typed by hand — which is how plots end up on a chamber PC where the '
        'audit will never see them. Omit the field to leave the value unchanged; '
        'send null to clear it, after which the node falls back to the workbook '
        'cell. platform:admin gated, like chamber registration.'
    ),
    'local_auth_login': (
        'Exchange an email and password for an access/refresh token pair. Public — '
        'a request presenting credentials has no principal yet. Every failure '
        '(unknown email, wrong password, disabled account, locked account) returns '
        'the SAME 401 code, body and — via a dummy hash verification — the same '
        'response time, so the endpoint cannot be used to enumerate staff.'
    ),
    'local_auth_refresh': (
        'Exchange a refresh token for a new token pair. Public because the refresh '
        'token is itself the credential. Permissions are re-read from the database '
        'on every refresh, so a revoked grant takes effect within one access-token '
        'lifetime rather than one refresh-token lifetime.'
    ),
    'local_auth_me': (
        'The authenticated caller\'s own profile. Reachable while a password change '
        'is pending, so the UI can render who is being asked to change it.'
    ),
    'local_auth_change_password': (
        'Change the caller\'s own password and receive a fresh token pair. The '
        'current password is re-verified even though the caller is authenticated: '
        'without that, a stolen access token would be account takeover. Succeeding '
        'increments session_version, which immediately invalidates every refresh '
        'token previously issued to this user.'
    ),
    'local_auth_logout': (
        'Revoke the presented access token. The refresh token dies with it. Note '
        'that revocation is process-local: a multi-process central deployment '
        'bounds an access token by its remaining lifetime instead.'
    ),
    'unlock_local_account': (
        'Lift a login lockout on a local account, as an administrator. The lock '
        'itself is not removable by the user: five wrong current-passwords lock '
        'the account for fifteen minutes, and a holder of a stolen access token '
        'can sustain that indefinitely by repeating it, so an account can be '
        'locked with no way out. Automatic expiry already exists; this is the '
        'other half the operator asked for. It clears the failure counters AND '
        'increments session_version, which ends every existing session for that '
        'account on every device — deliberate, because the lock is reachable by '
        'an attacker holding a token, and unlocking without it hands the account '
        'straight back to them. It is bounded rather than total: that stolen '
        'ACCESS token still works for its remaining lifetime — the access-token '
        'TTL this deployment configures (default 900s = 15 min) — so re-locking '
        'is delayed by that TTL, not prevented. If the account holds no lock the '
        'call is a 200 no-op: nothing is ended and nothing is audited. '
        'platform:admin gated, and audited as account.unlocked in the same '
        'transaction as the write.'
    ),
    'update_chamber_web_session_approval': (
        'Record whether a chamber is approved to accept web sessions. A chamber PC '
        'either takes web sessions or it does not — port approval is granted per '
        'machine, so the fleet converts one PC at a time and the two kinds run side '
        'by side. This records the APPROVAL only: it starts nothing, stops nothing, '
        'and refuses nothing. Whether the node actually opened a listener is observed '
        'from its heartbeat, and the disagreement between the two is what an operator '
        'reads — approved but silent means the rollout is incomplete (or nobody has '
        'logged in yet), serving without approval is a policy breach. Three-valued: '
        'send true/false to rule, send null to withdraw the ruling back to "nobody has '
        'decided" (not the same as false), omit the field to leave it unchanged. '
        'platform:admin gated — a tester must not be able to approve their own PC.'
    ),
    'get_chamber_equipment_config': (
        "A chamber's instrument connection settings — the analyzer / BT tester / "
        'switchbox GPIB and LAN addresses. Operator-scoped: this is the door the '
        'web screen reads through, so it is platform:read and carries no chamber '
        'token binding, unlike the node read on /settings. The map is opaque to '
        'the platform: its keys are whatever that provider declared in its UI '
        'descriptor, and interpreting them (which address wins, what to send once '
        'connected) stays in the provider node.'
    ),
    'update_chamber_equipment_config': (
        "Change a chamber's instrument connection settings. The body is a patch "
        'per KEY, not a replacement: a key you omit is left alone, a key sent as '
        'null is deleted, and a key sent as a string is set. The merge happens '
        'server-side in one locked transaction, which is what lets two testers '
        'edit two different fields of the same chamber at once without either '
        "losing the other's change — so send only the fields that were actually "
        'edited. platform:equipment-write gated: the person who knows the '
        "analyzer's new address after a re-cabling is the tester in the room, not "
        'an administrator. Takes effect on the node from its NEXT boot.'
    ),
    'get_chamber_settings': (
        'What this chamber node must configure itself with — the plot storage '
        'root and its instrument connection settings. Node-scoped: a chamber '
        'pulls this at boot, and the machine '
        'token is bound to the path chamber_id. Deliberately NOT platform:read, '
        'which would also hand a chamber PC coverage / claims / memberships and '
        'falsify the least-privilege property that a chamber token only sees its '
        'own row. An unreachable central leaves the node on its previous answer '
        'rather than stopping it. platform:chamber gated.'
    ),
    'push_artifact_custody_report': (
        'Report where a chamber found (or failed to find) its measurement plots. '
        'The plot originals are the audit evidence and they live on the company '
        'file server or the chamber PC — central cannot open either, so central '
        'does not judge: the node judges and reports, and this endpoint stores the '
        'verdict. Writes are latest-wins on observed_at (the time the NODE opened '
        'the storage roots), so a retried stale observation cannot overwrite a '
        'newer verdict; superseded sessions come back in the receipt rather than '
        'being silently dropped. platform:chamber gated and bound to the path '
        'chamber_id.'
    ),
    'get_project_artifact_custody': (
        'Whether this project\'s plots are where the audit will look for them — '
        'the project rollup plus one row per reported session. Blocking is never '
        'absorbed: one MISSING session makes the whole project MISSING, because '
        'the 2% missing at audit is a 100% problem. Sessions whose central row has '
        'no project_id yet are counted separately (unresolved_session_count); '
        'project sessions with no snapshot are counted separately as '
        'missing_snapshot_session_count. This keeps "no report exists" distinct '
        'from "a report exists but is not attributed" and from "what we can see '
        'is fine". Reports the OLDEST observation time — a green verdict resting on a '
        'three-week-old look is not "fine now". platform:read gated.'
    ),
    'get_artifact_custody_snapshot': (
        'Which specific plots are missing or diverged for one reported session, so '
        'a tester knows what to move rather than only that something is wrong. '
        'Verified rows are not listed (their tally is in counts) — one session is '
        'about a thousand plots and that list is not actionable. The snapshot must '
        'belong to the path project; otherwise 404, which does not reveal whether '
        'it exists elsewhere. platform:read gated.'
    ),
    'list_sample_inventory': (
        'List the authoritative web sample inventory with combined project, team, '
        'status, inclusive UTC as-of, and keyset filters. Deleted rows are excluded '
        'by default and included only when explicitly requested.'
    ),
    'create_sample': (
        'Create one web sample and its first complete revision; PM and test-team '
        'members use the same whole-record write permission.'
    ),
    'get_sample': 'Read one current sample, or its server-selected as-of snapshot.',
    'patch_sample': (
        'Patch any editable sample or latest-intake field with expected_version; '
        'the current row and immutable full revision are written atomically.'
    ),
    'change_sample_status': (
        'Change active/deleted status or restore a sample with an immutable revision '
        'and optimistic concurrency check.'
    ),
    'delete_sample': 'Soft-delete a sample by writing status=deleted; data remains recoverable.',
    'hard_delete_sample': (
        'Physically delete operational sample data for a global system administrator; '
        'measurement sessions and their snapshots remain preserved.'
    ),
    'list_sample_history': 'Read append-only full sample revisions with keyset pagination.',
    'export_sample_inventory': (
        'Export the same filtered inventory revision set as one of the two sanitized '
        'PM-status or RF-data XLSX template shapes.'
    ),
}


def _operation_summary(name: str) -> str:
    return _OPERATION_SUMMARIES.get(name, name.replace('_', ' '))


def _ref_object(schema_name: str) -> dict[str, Any]:
    return {'$ref': f'#/components/schemas/{schema_name}'}


def _path_parameters_for(path: str) -> list[dict[str, Any]]:
    """Path-parameter descriptors from ``PLATFORM_API_PATH_PARAMS`` SSOT.

    A ``{name}`` token with no SSOT entry raises immediately (loud) rather than
    emitting a guessed type.
    """
    parameters: list[dict[str, Any]] = []
    for name in iter_path_param_names(path):
        if name not in PLATFORM_API_PATH_PARAMS:
            raise KeyError(
                f"Path parameter '{name}' (in '{path}') has no entry in "
                f"PLATFORM_API_PATH_PARAMS — declare it in "
                f"`application.central_contract.api_contracts` SSOT."
            )
        parameters.append({
            'name': name,
            'in': 'path',
            'required': True,
            'schema': dict(PLATFORM_API_PATH_PARAMS[name]),
        })
    return parameters


def _build_responses_for(name: str) -> dict[str, Any]:
    operation = PLATFORM_API_OPERATIONS[name]
    response_schema_name = operation.get('response')
    if response_schema_name and response_schema_name in PLATFORM_API_SCHEMAS:
        ok_schema: dict[str, Any] = _ref_object(response_schema_name)
    else:
        ok_schema = {'type': 'object'}
    ok_response: dict[str, Any] = {
        'description': 'OK',
        'content': {
            operation.get('response_media_type', 'application/json'): {
                'schema': ok_schema,
            },
        },
    }
    headers = PLATFORM_API_RESPONSE_HEADERS.get(name)
    if headers:
        ok_response['headers'] = {
            header: dict(spec) for header, spec in headers.items()
        }
    # RFC 9457 (B1): default error responses advertise the shared problem+json
    # ProblemDetails body (descriptions byte-preserved).
    responses: dict[str, Any] = {
        '200': ok_response,
        '400': problem_error_response('Invalid project_id (not a valid uuid) or malformed cursor.'),
        '403': problem_error_response('AuthZ denied (missing required permission).'),
        '503': problem_error_response('Central backend unavailable (CentralReadError / ClaimWriteError).'),
    }
    # FE-P6-unify (2026-05-29): operation-specific error responses (write-op 409
    # claim conflict + membership 404) now come from the operation's
    # ``error_responses`` SSOT via the shared merge helper — the SAME mechanism
    # the headless builder uses. The old name-based ``if`` branches are retired
    # (data-driven, declared next to the operation in api_contracts).
    return apply_operation_error_responses(responses, operation)


def _build_request_body_for(name: str) -> dict[str, Any] | None:
    operation = PLATFORM_API_OPERATIONS[name]
    request_schema_name = operation.get('request')
    if not request_schema_name or request_schema_name not in PLATFORM_API_SCHEMAS:
        return None
    return {
        'required': True,
        'content': {
            'application/json': {
                'schema': _ref_object(request_schema_name),
            },
        },
    }


def _query_parameters_for(name: str) -> list[dict[str, Any]]:
    """Query-parameter descriptors from the PLATFORM_API_QUERY_PARAMS SSOT.

    A query param referenced by an operation but missing from the SSOT raises
    immediately (loud), mirroring the path-param contract.
    """
    parameters: list[dict[str, Any]] = []
    for param_name in PLATFORM_API_OPERATION_QUERY.get(name, ()):  # noqa: B009
        if param_name not in PLATFORM_API_QUERY_PARAMS:
            raise KeyError(
                f"Query parameter '{param_name}' (operation '{name}') has no entry "
                f"in PLATFORM_API_QUERY_PARAMS SSOT."
            )
        schema = PLATFORM_API_OPERATION_QUERY_OVERRIDES.get(name, {}).get(
            param_name, PLATFORM_API_QUERY_PARAMS[param_name]
        )
        parameters.append({
            'name': param_name,
            'in': 'query',
            'required': False,
            'schema': dict(schema),
        })
    return parameters


def _http_operation_schema(name: str, method: str, path: str) -> dict[str, Any]:
    operation_doc: dict[str, Any] = {
        'operationId': name,
        'summary': _operation_summary(name),
        'tags': ['platform'],
        'x-fcc-permission': PLATFORM_API_OPERATIONS[name]['permission'],
        'responses': _build_responses_for(name),
    }
    parameters = _path_parameters_for(path) + _query_parameters_for(name)
    if parameters:
        operation_doc['parameters'] = parameters
    request_body = _build_request_body_for(name)
    if request_body is not None:
        operation_doc['requestBody'] = request_body
    return {method.lower(): operation_doc}


def _resolve_permissions_header(config: Optional['PlatformApiConfig']) -> str:
    """Resolve the permissions HTTP header name from the ``HttpAuthConfig`` SSOT.

    ``None`` config (discovery callers) falls back to ``HttpAuthConfig()`` so the
    default flows from exactly one definition site.
    """
    if config is not None:
        return config.auth.auth_permissions_header
    from fcc_test_contracts.common.auth_config import HttpAuthConfig
    return HttpAuthConfig().auth_permissions_header


def build_platform_openapi_schema(
    config: Optional['PlatformApiConfig'] = None,
) -> dict[str, Any]:
    """Build the OpenAPI 3.1 schema for the Platform read API surface.

    WebSocket operations (``subscribe_chamber_progress``, 멀티챔버 P7/B4) appear
    under ``x-fcc-websocket-paths`` since OpenAPI 3.1 does not natively describe
    bidirectional WS streams; the matching AsyncAPI document
    (:func:`build_platform_asyncapi_schema`) carries the message catalog. This
    mirrors the session surface (``subscribe_session_events``).
    """
    paths: dict[str, dict[str, Any]] = {}
    ws_paths: dict[str, dict[str, Any]] = {}
    for name, (method, path) in PLATFORM_API_ROUTES.items():
        if method == 'WEBSOCKET':
            ws_paths[path] = {
                'operationId': name,
                'summary': _operation_summary(name),
                'x-fcc-permission': PLATFORM_API_OPERATIONS[name]['permission'],
                'x-fcc-asyncapi-channel': path,
            }
            continue
        paths.setdefault(path, {}).update(_http_operation_schema(name, method, path))

    permissions_header = _resolve_permissions_header(config)

    return {
        'openapi': '3.1.0',
        'info': {
            'title': PLATFORM_API_TITLE,
            'version': PLATFORM_API_CONTRACT_VERSION,
            'x-fcc-api-compatibility-major': PLATFORM_API_COMPATIBILITY_MAJOR,
            'description': (
                'FCC Platform central read model surface. Project-wide coverage '
                '(coverage_by_condition_hash) + active claims (active_claims) read '
                'from the central database — the cross-engineer source of truth '
                'distinct from the per-engineer Headless API. Authorization uses '
                'trusted-header permission tokens (see '
                '``components.securitySchemes.FccPlatformPermissions``).'
            ),
        },
        'paths': paths,
        'components': {
            # RFC 9457 (B1): merge the ProblemDetails + ErrorCode schemas from the
            # same SSOT as the headless surface, narrowed to the codes THIS
            # surface can emit (``ERROR_CODE_SURFACE_SCOPE``) — a platform-scoped
            # code must not leak into the headless artifact.
            'schemas': build_components_schemas({
                **problem_details_component_schemas(
                    surface_error_codes(ApiSurface.PLATFORM)
                ),
                **PLATFORM_API_SCHEMAS,
            }),
            'securitySchemes': {
                'FccPlatformPermissions': {
                    'type': 'apiKey',
                    'in': 'header',
                    'name': permissions_header,
                    'description': (
                        'Comma-separated permission tokens. See the '
                        '``x-fcc-permissions`` catalog for available values.'
                    ),
                },
            },
            'x-fcc-permissions': {
                permission: PLATFORM_API_PERMISSION_DESCRIPTIONS[permission]
                for permission in sorted(set(PLATFORM_API_PERMISSIONS.values()))
            },
        },
        'x-fcc-websocket-paths': ws_paths,
    }


def build_platform_asyncapi_schema(
    config: Optional['PlatformApiConfig'] = None,
) -> dict[str, Any]:
    """Build an AsyncAPI 3.0 schema for the Platform chamber-progress WS stream.

    Channel: ``/platform/chambers/events`` (멀티챔버 P7/B4, ADR-0015 Option B —
    central progress relay). One ``chamber_progress`` message variant carrying the
    ``ChamberProgressEvent`` wire shape (``ChamberSessionProgress`` reused from C1)
    plus the ``ping`` heartbeat the WS handler emits. Mirrors
    ``build_session_asyncapi_schema`` so the two real-time surfaces cannot drift on
    document shape. The artifact (``docs/api/platform-api.asyncapi.json``) MUST be
    byte-identical to this builder output (CI drift gate via
    ``scripts/export_session_api_schemas.py --verify``).
    """
    ws_path = PLATFORM_API_ROUTES['subscribe_chamber_progress'][1]

    progress_message = {
        'name': 'ChamberProgressEvent',
        'title': 'Chamber progress event',
        'summary': (
            'A single chamber measurement progress snapshot fanned out from a '
            'node heartbeat (C1 heartbeat-carried progress).'
        ),
        'contentType': 'application/json',
        'payload': _ref_object('ChamberProgressEvent'),
    }
    ping_message = {
        'name': 'ChamberProgressPing',
        'title': 'Keep-alive ping',
        'summary': (
            'Periodic server ping used to detect half-open / dead client sockets '
            '(mirror of the session WS heartbeat).'
        ),
        'contentType': 'application/json',
        'payload': {
            'type': 'object',
            'required': ['kind'],
            'properties': {
                'kind': {'type': 'string', 'const': 'ping'},
                'payload': {'type': 'array', 'items': {}},
                'connection_id': {'type': 'string'},
            },
            'additionalProperties': True,
        },
    }
    messages = {
        progress_message['name']: progress_message,
        ping_message['name']: ping_message,
    }

    title = PLATFORM_API_TITLE
    version = PLATFORM_API_CONTRACT_VERSION

    return {
        'asyncapi': '3.0.0',
        'info': {
            'title': f'{title} Chamber Progress Events',
            'version': version,
            'x-fcc-api-compatibility-major': PLATFORM_API_COMPATIBILITY_MAJOR,
            'description': (
                'Real-time chamber measurement progress relay (멀티챔버 P7/B4). The '
                'central hub fans out node heartbeat-carried progress (ADR-0015 '
                'Option B — node push). Subscribe via the WebSocket channel below; '
                'the GET /platform/chambers polling read remains the fallback. '
                'AuthZ (W3-4, 2026-08-01): browser `WebSocket` cannot set a custom '
                '`Authorization` header on the upgrade request, so the bearer '
                'credential rides the `Sec-WebSocket-Protocol` offer instead — '
                f'`[{WS_BEARER_SUBPROTOCOL!r}, <token>]` (RFC 6455 §4.1). See '
                '`x-fcc-ws-bearer-subprotocol` on the channel below. A genuine '
                '`Authorization` header (non-browser clients) always takes '
                'precedence over this overlay.'
            ),
        },
        'channels': {
            ws_path: {
                'address': ws_path,
                'description': (
                    'WebSocket channel emitting real-time chamber progress events. '
                    'Server sends JSON frames; clients must not send messages. '
                    'Offer the bearer credential as a WebSocket subprotocol pair — '
                    f'`[{WS_BEARER_SUBPROTOCOL!r}, <token>]` — the server echoes only '
                    'the sentinel (never the token) back via the handshake response.'
                ),
                'x-fcc-ws-bearer-subprotocol': WS_BEARER_SUBPROTOCOL,
                'messages': {
                    name: {'$ref': f'#/components/messages/{name}'}
                    for name in messages
                },
            },
        },
        'operations': {
            'subscribeChamberProgress': {
                'action': 'receive',
                'channel': {'$ref': f'#/channels/{ws_path}'},
                'summary': 'Receive real-time chamber progress events from the hub.',
                'x-fcc-permission': (
                    PLATFORM_API_OPERATIONS['subscribe_chamber_progress']['permission']
                ),
            },
        },
        'components': {
            'messages': messages,
            'schemas': build_components_schemas({
                'ChamberProgressEvent': PLATFORM_API_SCHEMAS['ChamberProgressEvent'],
                'ChamberSessionProgress': PLATFORM_API_SCHEMAS['ChamberSessionProgress'],
            }),
        },
    }
