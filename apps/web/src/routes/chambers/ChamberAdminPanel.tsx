import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';

import {
  registerChamber,
  updateChamberWebSessionApproval,
  type ChamberAvailabilityEnvelope,
  type RegisterChamberRequest,
} from '@/api/platform-client';
import { queryKeys } from '@/api/query-config';
import { useT } from '@/i18n';
import {
  formatHeartbeatAge,
  heartbeatAgeSeconds,
  type ServerClockAnchor,
} from '@/shared/heartbeat-age';
import { Button, DataTable, describeApiError, ErrorState, SectionBand, StatusMessage } from '@/ui';

import { chamberUnavailableReasonLabel } from './status';

interface ChamberAdminPanelProps {
  readonly chambers: readonly ChamberAvailabilityEnvelope[];
  /**
   * M4 — the payload's server instant paired with the client reading taken when
   * it landed. The panel receives the anchor and the ticking clock rather than a
   * bare `serverTime`, because a bare `serverTime` is exactly what froze the age
   * cell: it is a fetch-time constant, so nothing derived from it alone can move.
   */
  readonly clockAnchor: ServerClockAnchor;
  /** Current clock reading, subscribed ONCE by the workbench and passed down. */
  readonly nowMs: number;
}

/**
 * `<select>` 값 ↔ 3-상태 승인.
 *
 * DOM select 값은 문자열뿐이라 `null`/`true`/`false` 를 그대로 담을 수 없다. 세 토큰을
 * 한 곳에서 정의하고 양방향 변환을 붙여, 화면 어디에서도 `''` 같은 빈 문자열이 곧
 * "미승인"으로 읽히지 않게 한다 — 그 접힘이 이 축이 없애려는 바로 그것이다.
 */
const APPROVAL_UNSET = 'unset';
const APPROVAL_YES = 'yes';
const APPROVAL_NO = 'no';

function approvalToken(value: boolean | null | undefined): string {
  if (value === true) return APPROVAL_YES;
  if (value === false) return APPROVAL_NO;
  return APPROVAL_UNSET;
}

function approvalFromToken(token: string): boolean | null {
  if (token === APPROVAL_YES) return true;
  if (token === APPROVAL_NO) return false;
  return null;
}

interface ChamberDraft {
  readonly name: string;
  readonly baseUrl: string;
  readonly ttl: string;
  readonly enabled: boolean;
}

/**
 * W2-C M1 — one chamber's UNSAVED local edit.
 *
 * `draft` is what the operator typed; `baseline` is the server-derived draft as
 * it stood when the edit began. Keeping the baseline is what lets the panel tell
 * "the operator changed this" apart from "the server changed underneath the
 * operator" without ever consulting a mutable ref or an effect.
 */
interface ChamberEdit {
  readonly draft: ChamberDraft;
  readonly baseline: ChamberDraft;
}

interface BootstrapDraft {
  readonly chamberId: string;
  readonly name: string;
  readonly baseUrl: string;
  readonly ttl: string;
}

const EMPTY_BOOTSTRAP_DRAFT: BootstrapDraft = {
  chamberId: '',
  name: '',
  baseUrl: '',
  ttl: '90',
};

function bootstrapPayloadFor(draft: BootstrapDraft): RegisterChamberRequest {
  return {
    chamber_id: draft.chamberId.trim(),
    name: draft.name.trim(),
    base_url: draft.baseUrl.trim(),
    enabled: true,
    heartbeat_ttl_seconds: Number(draft.ttl),
  };
}

function draftFrom(chamber: ChamberAvailabilityEnvelope): ChamberDraft {
  return {
    name: chamber.name ?? '',
    baseUrl: chamber.base_url,
    ttl: String(chamber.heartbeat_ttl_seconds),
    enabled: chamber.enabled,
  };
}

function sameDraft(a: ChamberDraft, b: ChamberDraft): boolean {
  return a.name === b.name && a.baseUrl === b.baseUrl && a.ttl === b.ttl && a.enabled === b.enabled;
}

function payloadFor(
  chamber: ChamberAvailabilityEnvelope,
  draft: ChamberDraft,
  enabled = draft.enabled,
): RegisterChamberRequest {
  const ttl = Number.parseInt(draft.ttl, 10);
  const request: RegisterChamberRequest = {
    chamber_id: chamber.chamber_id,
    name: draft.name.trim(),
    base_url: draft.baseUrl.trim(),
    enabled,
    heartbeat_ttl_seconds: Number.isFinite(ttl) && ttl > 0 ? ttl : chamber.heartbeat_ttl_seconds,
  };
  return request;
}

export function ChamberAdminPanel({
  chambers,
  clockAnchor,
  nowMs,
}: ChamberAdminPanelProps): JSX.Element | null {
  const { t } = useT();
  const queryClient = useQueryClient();
  // W2-C M1 — LOCAL EDITS ONLY, never a mirror of the server payload.
  //
  // The panel used to hold a full `Record<chamberId, ChamberDraft>` copy of the
  // server list and re-seed it from an unconditional `useEffect([chambers])`.
  // Once W2-B raised the chambers query to the MONITORED tier (45s poll + focus
  // refetch) that effect fired on a live cadence — and because `last_heartbeat_at`
  // moves on every poll, `chambers` was a new reference every time, so an operator
  // typing into any field lost the input roughly every 45 seconds.
  //
  // The fix removes the mirror rather than gating it: state holds ONLY the
  // chambers the operator has actually edited, and the rendered value is derived
  // per row as `edit?.draft ?? draftFrom(chamber)`. Consequences:
  //   - an edited row keeps the operator's text across any number of refetches,
  //   - an untouched row still reads straight from the server every render (the
  //     freshness W2-B bought is NOT traded away for edit safety),
  //   - there is no sync effect left to mis-condition later.
  const [edits, setEdits] = useState<Record<string, ChamberEdit>>({});
  const [success, setSuccess] = useState<string | null>(null);
  const [bootstrapDraft, setBootstrapDraft] = useState<BootstrapDraft>(EMPTY_BOOTSTRAP_DRAFT);
  const [bootstrapValidation, setBootstrapValidation] = useState<string | null>(null);
  const [bootstrapSubmission, setBootstrapSubmission] = useState(false);

  function discardEdit(chamberId: string): void {
    setEdits((current) => {
      if (current[chamberId] === undefined) return current;
      const next = { ...current };
      delete next[chamberId];
      return next;
    });
  }

  const mutation = useMutation({
    mutationFn: registerChamber,
    onSuccess: (saved) => {
      setSuccess(
        t(
          bootstrapSubmission ? 'routes.chambers.bootstrapSuccess' : 'routes.chambers.adminSuccess',
          { chamber: saved.chamber_id },
        ),
      );
      if (bootstrapSubmission) {
        setBootstrapDraft(EMPTY_BOOTSTRAP_DRAFT);
        setBootstrapSubmission(false);
      }
      // The write landed, so this chamber's local edit is no longer "unsaved
      // work to protect" — drop it and let the row follow the server again.
      // Without this the override would outlive its purpose and freeze the row.
      discardEdit(saved.chamber_id);
      void queryClient.invalidateQueries({ queryKey: queryKeys.chambers.list() });
    },
  });

  // 챔버 모드 축 (2026-08-16) — 승인은 **등록과 다른 쓰기**다.
  //
  // ⚠️ 위 `registerChamber` draft 에 얹지 않는 것이 요점이다. 등록 요청은 승인 칸을
  // 실을 수 없고(중앙이 그 컬럼을 등록 쓰기 목록에서 아예 뺐다 — 노드는 자기가
  // 승인됐는지 모른다), 이 패널은 **등록 재-POST 로** 챔버를 편집하므로 얹으면 값이
  // 조용히 버려지고 화면만 저장됐다고 말한다. 그래서 자기 mutation 을 갖는다.
  const approvalMutation = useMutation({
    mutationFn: ({ chamberId, approval }: { chamberId: string; approval: boolean | null }) =>
      updateChamberWebSessionApproval(chamberId, approval),
    onSuccess: (saved) => {
      setSuccess(t('routes.chambers.approvalSaved'));
      void queryClient.invalidateQueries({ queryKey: queryKeys.chambers.list() });
      return saved;
    },
  });

  if (chambers.length === 0) {
    function updateBootstrap(next: Partial<BootstrapDraft>): void {
      setBootstrapDraft((current) => ({ ...current, ...next }));
      setBootstrapValidation(null);
      setBootstrapSubmission(false);
      setSuccess(null);
      mutation.reset();
    }

    function submitBootstrap(event: FormEvent<HTMLFormElement>): void {
      event.preventDefault();
      const draft = bootstrapDraft;
      const ttl = Number(draft.ttl);
      let validBaseUrl = false;
      try {
        const parsed = new URL(draft.baseUrl.trim());
        validBaseUrl = parsed.protocol === 'http:' || parsed.protocol === 'https:';
      } catch {
        validBaseUrl = false;
      }
      if (
        draft.chamberId.trim() === '' ||
        draft.name.trim() === '' ||
        !validBaseUrl ||
        !Number.isInteger(ttl) ||
        ttl <= 0
      ) {
        setBootstrapValidation(t('routes.chambers.bootstrapValidation'));
        return;
      }
      setBootstrapValidation(null);
      setBootstrapSubmission(true);
      mutation.mutate(bootstrapPayloadFor(draft));
    }

    return (
      <section
        aria-labelledby="chambers-admin-bootstrap-heading"
        data-testid="chambers-admin-bootstrap"
      >
        <SectionBand
          title={t('routes.chambers.bootstrapTitle')}
          titleId="chambers-admin-bootstrap-heading"
        />
        <p className="section-hint">{t('routes.chambers.bootstrapDescription')}</p>
        {success !== null && (
          <StatusMessage message={success} tone="success" testId="chambers-admin-success" />
        )}
        {bootstrapValidation !== null && (
          <ErrorState message={bootstrapValidation} testId="chambers-admin-bootstrap-validation" />
        )}
        {mutation.isError && (
          <ErrorState
            testId="chambers-admin-error"
            message={describeApiError(mutation.error, 'platform', {
              forbidden: t('routes.chambers.bootstrapForbidden'),
              network: t('routes.chambers.bootstrapNetwork'),
              default: t('routes.chambers.bootstrapFailed'),
            })}
          />
        )}
        <form
          aria-label={t('routes.chambers.bootstrapAria')}
          data-testid="chambers-admin-bootstrap-form"
          onSubmit={submitBootstrap}
        >
          <p>{t('routes.chambers.bootstrapHelp')}</p>
          <label htmlFor="chambers-bootstrap-id">
            {t('routes.chambers.bootstrapChamberIdLabel')}
          </label>
          <input
            id="chambers-bootstrap-id"
            data-testid="chambers-bootstrap-id"
            required
            value={bootstrapDraft.chamberId}
            onChange={(event) => updateBootstrap({ chamberId: event.currentTarget.value })}
          />
          <label htmlFor="chambers-bootstrap-name">{t('routes.chambers.bootstrapNameLabel')}</label>
          <input
            id="chambers-bootstrap-name"
            data-testid="chambers-bootstrap-name"
            required
            value={bootstrapDraft.name}
            onChange={(event) => updateBootstrap({ name: event.currentTarget.value })}
          />
          <label htmlFor="chambers-bootstrap-base-url">
            {t('routes.chambers.bootstrapBaseUrlLabel')}
          </label>
          <input
            id="chambers-bootstrap-base-url"
            data-testid="chambers-bootstrap-base-url"
            required
            type="url"
            value={bootstrapDraft.baseUrl}
            onChange={(event) => updateBootstrap({ baseUrl: event.currentTarget.value })}
          />
          <label htmlFor="chambers-bootstrap-ttl">{t('routes.chambers.bootstrapTtlLabel')}</label>
          <input
            id="chambers-bootstrap-ttl"
            data-testid="chambers-bootstrap-ttl"
            min="1"
            required
            step="1"
            type="number"
            value={bootstrapDraft.ttl}
            onChange={(event) => updateBootstrap({ ttl: event.currentTarget.value })}
          />
          <Button
            type="submit"
            variant="primary"
            data-testid="chambers-bootstrap-submit"
            disabled={mutation.isPending}
          >
            {mutation.isPending
              ? t('routes.chambers.bootstrapSubmitting')
              : t('routes.chambers.bootstrapSubmit')}
          </Button>
        </form>
      </section>
    );
  }

  function updateDraft(chamber: ChamberAvailabilityEnvelope, next: Partial<ChamberDraft>): void {
    setEdits((current) => {
      const server = draftFrom(chamber);
      const existing = current[chamber.chamber_id];
      return {
        ...current,
        [chamber.chamber_id]: {
          // Captured once, at the first keystroke: the server value the operator
          // was looking at when they started editing.
          baseline: existing?.baseline ?? server,
          draft: { ...(existing?.draft ?? server), ...next },
        },
      };
    });
    setSuccess(null);
    mutation.reset();
  }

  function save(chamber: ChamberAvailabilityEnvelope): void {
    const draft = edits[chamber.chamber_id]?.draft ?? draftFrom(chamber);
    mutation.mutate(payloadFor(chamber, draft));
  }

  function toggle(chamber: ChamberAvailabilityEnvelope): void {
    const draft = edits[chamber.chamber_id]?.draft ?? draftFrom(chamber);
    mutation.mutate(payloadFor(chamber, draft, !draft.enabled));
  }

  return (
    <section aria-labelledby="chambers-admin-heading" data-testid="chambers-admin">
      <SectionBand title={t('routes.chambers.sectionAdmin')} titleId="chambers-admin-heading" />
      {success !== null && (
        <StatusMessage message={success} tone="success" testId="chambers-admin-success" />
      )}
      {mutation.isError && (
        <ErrorState
          testId="chambers-admin-error"
          message={describeApiError(mutation.error, 'platform', {
            forbidden: t('routes.chambers.adminForbidden'),
            network: t('routes.chambers.adminNetwork'),
            default: t('routes.chambers.adminFailed'),
          })}
        />
      )}
      <DataTable
        testId="chambers-admin-table"
        caption={t('routes.chambers.adminTableCaption')}
        head={
          <thead>
            <tr>
              <th scope="col">{t('routes.chambers.colName')}</th>
              <th scope="col">{t('routes.chambers.colBaseUrl')}</th>
              <th scope="col">{t('routes.chambers.colTtl')}</th>
              <th scope="col">{t('routes.chambers.colEnabled')}</th>
              <th scope="col">{t('routes.chambers.colHeartbeatAge')}</th>
              <th scope="col">{t('routes.chambers.colLastError')}</th>
              <th scope="col">{t('routes.chambers.approvalLabel')}</th>
              <th scope="col">{t('routes.chambers.colAdminActions')}</th>
            </tr>
          </thead>
        }
        body={
          <tbody>
            {chambers.map((chamber) => {
              const serverDraft = draftFrom(chamber);
              const edit = edits[chamber.chamber_id];
              // Derived every render — an untouched chamber has no override and
              // therefore tracks the server payload with zero staleness.
              const draft = edit?.draft ?? serverDraft;
              // The operator is holding work the server does not have yet.
              const unsaved = edit !== undefined && !sameDraft(edit.draft, serverDraft);
              // A refetch moved this chamber's registration WHILE it was being
              // edited. The override deliberately wins on screen, so the incoming
              // server value would otherwise be discarded silently — contract M1
              // forbids exactly that, so it is surfaced with a way out.
              const serverMovedUnderEdit =
                edit !== undefined && !sameDraft(edit.baseline, serverDraft);
              return (
                <tr key={chamber.chamber_id} data-testid="chambers-admin-row">
                  <th scope="row">
                    <input
                      aria-label={t('routes.chambers.adminNameLabel')}
                      data-testid="chambers-admin-name"
                      type="text"
                      value={draft.name}
                      onChange={(event) =>
                        updateDraft(chamber, { name: event.currentTarget.value })
                      }
                    />
                    <small>{chamber.chamber_id}</small>
                    {unsaved && (
                      <small data-testid="chambers-admin-unsaved">
                        {t('routes.chambers.adminUnsavedBadge')}
                      </small>
                    )}
                    {serverMovedUnderEdit && (
                      <small data-testid="chambers-admin-server-changed">
                        {t('routes.chambers.adminServerChanged')}
                      </small>
                    )}
                    {edit !== undefined && (
                      <Button
                        data-testid="chambers-admin-discard"
                        type="button"
                        variant="ghost"
                        onClick={() => discardEdit(chamber.chamber_id)}
                      >
                        {t('routes.chambers.adminDiscardEdit')}
                      </Button>
                    )}
                  </th>
                  <td>
                    <input
                      aria-label={t('routes.chambers.adminBaseUrlLabel')}
                      data-testid="chambers-admin-base-url"
                      type="url"
                      value={draft.baseUrl}
                      onChange={(event) =>
                        updateDraft(chamber, { baseUrl: event.currentTarget.value })
                      }
                    />
                  </td>
                  <td>
                    <input
                      aria-label={t('routes.chambers.adminTtlLabel')}
                      data-testid="chambers-admin-ttl"
                      min="1"
                      step="1"
                      type="number"
                      value={draft.ttl}
                      onChange={(event) => updateDraft(chamber, { ttl: event.currentTarget.value })}
                    />
                  </td>
                  <td>
                    <label>
                      <input
                        data-testid="chambers-admin-enabled"
                        type="checkbox"
                        checked={draft.enabled}
                        onChange={(event) =>
                          updateDraft(chamber, { enabled: event.currentTarget.checked })
                        }
                      />
                      {draft.enabled
                        ? t('routes.chambers.adminEnabled')
                        : t('routes.chambers.adminDisabled')}
                    </label>
                  </td>
                  {/* 승인은 3-상태다 — `미판정`과 `미승인`은 다른 값이고 운영자가
                    할 일이 다르다. 그래서 체크박스가 아니라 select 다: 체크박스는
                    그 셋을 표현할 수 없고, 표현할 수 없는 것을 표현하는 척하면
                    "아무도 판정 안 함"이 조용히 "미승인"이 된다. */}
                  <td>
                    <select
                      aria-label={t('routes.chambers.approvalLabel')}
                      data-testid="chambers-admin-approval"
                      value={approvalToken(chamber.accepts_web_sessions)}
                      onChange={(event) =>
                        approvalMutation.mutate({
                          chamberId: chamber.chamber_id,
                          approval: approvalFromToken(event.currentTarget.value),
                        })
                      }
                    >
                      <option value={APPROVAL_UNSET}>{t('routes.chambers.approvalUnset')}</option>
                      <option value={APPROVAL_YES}>{t('routes.chambers.approvalYes')}</option>
                      <option value={APPROVAL_NO}>{t('routes.chambers.approvalNo')}</option>
                    </select>
                  </td>
                  <td data-testid="chambers-admin-heartbeat-age">
                    {formatHeartbeatAge(
                      heartbeatAgeSeconds(clockAnchor, chamber.last_heartbeat_at, nowMs),
                    )}
                  </td>
                  <td data-testid="chambers-admin-last-error">
                    {chamber.last_error !== null &&
                    chamber.last_error !== undefined &&
                    chamber.last_error !== '' ? (
                      <span data-testid="chambers-admin-last-error-message">
                        {chamber.last_error}
                      </span>
                    ) : (
                      <span>{t('routes.chambers.adminLastErrorNone')}</span>
                    )}
                    {chamber.unavailable_reason !== null &&
                      chamber.unavailable_reason !== undefined && (
                        <small data-testid="chambers-admin-unavailable-reason">
                          {chamberUnavailableReasonLabel(t, chamber.unavailable_reason)}
                        </small>
                      )}
                  </td>
                  <td>
                    <Button
                      data-testid="chambers-admin-save"
                      type="button"
                      variant="primary"
                      disabled={mutation.isPending}
                      onClick={() => save(chamber)}
                    >
                      {mutation.isPending
                        ? t('routes.chambers.adminSaving')
                        : t('routes.chambers.adminSave')}
                    </Button>
                    <Button
                      data-testid="chambers-admin-toggle"
                      type="button"
                      variant="secondary"
                      disabled={mutation.isPending}
                      onClick={() => toggle(chamber)}
                    >
                      {draft.enabled
                        ? t('routes.chambers.adminDisable')
                        : t('routes.chambers.adminEnable')}
                    </Button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        }
      />
    </section>
  );
}
