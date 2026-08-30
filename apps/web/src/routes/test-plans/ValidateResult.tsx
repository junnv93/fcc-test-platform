import { useT } from '@/i18n';
import { DataTable, StatusMessage } from '@/ui';

import { type ValidateResponse, type ValidationIssue } from './types';
import { orDash } from './util';

/**
 * Validation result panel — error/warning counts plus the issue rows the
 * recompute surfaced. A clean draft (0/0) renders a success message; otherwise
 * the issues table lists each issue's severity / type / message / capability
 * path so the operator can fix what would block a publish.
 *
 * W2-C M3 — `stale` marks a result whose draft has changed since the run. The
 * panel then refuses to present it as a verdict: the "no issues" success message
 * is withdrawn (a clean run over rows that no longer exist is the single most
 * misleading thing this screen can show), and the retained detail is labelled as
 * a record of an earlier run. The result is kept rather than discarded because
 * the issues it found are still useful working material — what must not survive
 * is the CLAIM that they describe the current draft.
 */
export function ValidateResult({
  result,
  stale = false,
}: {
  readonly result: ValidateResponse;
  readonly stale?: boolean;
}): JSX.Element {
  const { t } = useT();
  const issues: readonly ValidationIssue[] = result.issues ?? [];
  const clean = !stale && result.error_count === 0 && result.warning_count === 0;
  const groups = issues.reduce<Record<string, number>>((acc, issue) => {
    const key = `${orDash(issue.severity)} / ${orDash(issue.issue_type)}`;
    acc[key] = (acc[key] ?? 0) + 1;
    return acc;
  }, {});
  return (
    <section data-testid="test-plans-validate-result" data-stale={stale ? 'true' : undefined}>
      {stale && (
        <StatusMessage
          tone="info"
          testId="test-plans-validate-stale"
          message={t('routes.testPlans.validateStaleNotice')}
        />
      )}
      {clean ? (
        <StatusMessage
          tone="success"
          testId="test-plans-validate-clean"
          message={t('routes.testPlans.validateNoIssues')}
        />
      ) : (
        <StatusMessage
          tone="info"
          testId="test-plans-validate-summary"
          message={t('routes.testPlans.validateResult', {
            errors: result.error_count,
            warnings: result.warning_count,
          })}
        />
      )}
      {issues.length > 0 && (
        <DataTable
          testId="test-plans-validate-groups"
          caption={t('routes.testPlans.validateGroupsCaption')}
          head={
            <thead>
              <tr>
                <th scope="col">{t('routes.testPlans.colIssueGroup')}</th>
                <th scope="col">{t('routes.testPlans.colIssueCount')}</th>
              </tr>
            </thead>
          }
          body={
            <tbody>
              {Object.entries(groups).map(([group, count]) => (
                <tr key={group} data-testid="test-plans-validate-group">
                  <th scope="row">{group}</th>
                  <td>{count}</td>
                </tr>
              ))}
            </tbody>
          }
        />
      )}
      {issues.length > 0 && (
        <DataTable
          testId="test-plans-validate-issues"
          caption={t('routes.testPlans.validateIssuesCaption')}
          head={
            <thead>
              <tr>
                <th scope="col">{t('routes.testPlans.colIssueSeverity')}</th>
                <th scope="col">{t('routes.testPlans.colIssueType')}</th>
                <th scope="col">{t('routes.testPlans.colIssueMessage')}</th>
                <th scope="col">{t('routes.testPlans.colIssuePath')}</th>
              </tr>
            </thead>
          }
          body={
            <tbody>
              {issues.map((issue, index) => (
                <tr key={index} data-testid="test-plans-validate-issue">
                  <td>{orDash(issue.severity)}</td>
                  <td>{orDash(issue.issue_type)}</td>
                  <td>{orDash(issue.message)}</td>
                  <td>{orDash((issue.capability_path ?? []).join(' / '))}</td>
                </tr>
              ))}
            </tbody>
          }
        />
      )}
    </section>
  );
}
