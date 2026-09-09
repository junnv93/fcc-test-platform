/**
 * The test items under one family (flowdeck-console-ui, operator pass).
 *
 * One row per planned condition, in one of three states:
 *
 *   🔒 진행 중 — held in `active_claims`. Who is on it right now.
 *   ✅ 완료   — measured. Who, when, which attempt, what verdict.
 *   ⬜ 미착수 — planned, and in neither of the above.
 *
 * ⚠️ Grouped by MODE, not sorted flat. Sorting by state put the same mode in
 * three places (its held rows at the top, its measured rows in the middle, its
 * remaining rows at the bottom), so "what is left on 802.11n HT40" meant
 * gathering the answer from three parts of a 72-row list. A mode is the unit a
 * person sets the chamber up for; keeping its rows together is what makes the
 * list usable as a plan rather than a log.
 *
 * 🔴 2026-09-09 — the not-started state used to be a COUNT, not rows. The plan
 * was only reachable through the progress read, which groups conditions by mode,
 * so a condition still to be done had no name anywhere in the read surface.
 * `list_project_plan_conditions` now returns the plan rows themselves and every
 * state is a row carrying its `test_item` — POWER / PSD / OBW / CBE / CSE / … —
 * which is the unit an operator actually schedules by.
 *
 * ⚠️ Still absent: the axes that pick ONE condition out of several under the
 * same test item (channel, bandwidth, antenna, modulation). Those live on the
 * kernel's `TestPlanRow` and stop at plan publication. When a test item carries
 * more than one condition, this table shows rows that read identically — the
 * count is right and the identity is not. Stated, never papered over.
 *
 * ⚠️ Read-only. Claiming and starting belong to the working screens; a viewer
 * that also writes stops being a place you can trust to describe the world.
 */

export type ItemState = 'held' | 'done' | 'todo';

export interface ItemRow {
  readonly id: string;
  readonly mode: string;
  /** POWER / PSD / OBW / … — from the plan row, joined on `condition_hash`. */
  readonly item: string;
  readonly state: ItemState;
  /** Who measured it, or who holds it. Empty for a not-started row. */
  readonly operator: string;
  /** ISO instant of the measurement or the claim. Empty when not started. */
  readonly when: string;
  readonly verdict: string | null;
  readonly attempt: number;
  readonly minutes: number | null;
}

export interface ItemBundle {
  readonly rows: readonly ItemRow[];
  /** True when more than one planned condition shares a (mode, test item) —
   *  i.e. some rows cannot be told apart by anything this read carries. */
  readonly hasAmbiguousRows: boolean;
}

/** `all` shows everything; the other two are the two questions an operator
 *  actually arrives with — "what is left" and "what has to be redone". */
export type ItemFilter = 'all' | 'todo' | 'failed';

export interface ItemTableProps {
  readonly items: ItemBundle | undefined;
  readonly filter: ItemFilter;
  readonly t: (key: string, vars?: Record<string, string>) => string;
  readonly testId?: string;
}

const FAILED = new Set(['fail', 'failed', 'nonconforming', 'ng']);

/** `2026-09-12T01:31:00Z` → `09-12 01:31`. The year is the same for every row
 *  on this screen, so printing it 72 times buys nothing. */
function shortWhen(value: string): string {
  if (value === '') return '';
  return `${value.slice(5, 10)} ${value.slice(11, 16)}`;
}

function matches(row: ItemRow, filter: ItemFilter): boolean {
  if (filter === 'all') return true;
  if (filter === 'todo') return row.state === 'todo';
  return row.verdict !== null && FAILED.has(row.verdict);
}

export function ItemTable({ items, filter, t, testId }: ItemTableProps): JSX.Element {
  if (items === undefined) {
    return (
      <p className="item-table__empty" data-testid={testId}>
        {t('routes.home.items.none')}
      </p>
    );
  }

  /* 모드별로 묶는다. 순서는 «계획 순»이 아니라 처음 나타난 순 — 호출부가 이미
     (모드, 항목)으로 정렬해 넘기므로 여기서 다시 정렬하면 그 결정이 두 군데가
     된다. 모드 안의 행 순서도 호출부의 것을 그대로 쓴다. */
  const groups = new Map<string, ItemRow[]>();
  for (const row of items.rows) {
    const bucket = groups.get(row.mode) ?? [];
    bucket.push(row);
    groups.set(row.mode, bucket);
  }

  const visible = [...groups.entries()]
    .map(([mode, rows]) => ({
      mode,
      rows: rows.filter((row) => matches(row, filter)),
      /* 진척은 «필터와 무관하게» 그 모드 전체에서 센다. 「미착수만」을 켰다고
         5/9 가 0/4 로 바뀌면, 필터가 사실을 바꾼 것처럼 읽힌다. */
      done: rows.filter((row) => row.state === 'done').length,
      total: rows.length,
    }))
    .filter((group) => group.rows.length > 0);

  if (visible.length === 0) {
    return (
      <p className="item-table__empty" data-testid={testId}>
        {t('routes.home.items.noneForFilter')}
      </p>
    );
  }

  return (
    <div className="item-table" data-testid={testId}>
      <table>
        <thead>
          <tr>
            <th scope="col">{t('routes.home.items.colState')}</th>
            <th scope="col">{t('routes.home.items.colItem')}</th>
            <th scope="col">{t('routes.home.items.colWho')}</th>
            <th scope="col" className="item-table__num">
              {t('routes.home.items.colPlanned')}
            </th>
          </tr>
        </thead>

        {/* tbody 하나가 모드 하나다. 그룹 머리가 `<th scope="colgroup">` 인
            이유: 스크린리더에서 아래 행들이 어느 모드에 속하는지 구조로 전달된다
            — 시각적으로는 굵은 줄 하나지만 의미는 「이 아래는 이것들」이다. */}
        {visible.map((group) => (
          <tbody className="item-group" key={group.mode}>
            <tr className="item-group__head">
              <th scope="colgroup" colSpan={3}>
                {group.mode}
              </th>
              <td className="item-table__num item-group__count">
                {t('routes.home.items.groupCount', {
                  done: String(group.done),
                  total: String(group.total),
                })}
              </td>
            </tr>

            {group.rows.map((row) => (
              <tr
                className="item-row"
                data-state={row.state}
                data-verdict={row.verdict ?? undefined}
                key={row.id}
              >
                <td>
                  <span className="item-row__dot" aria-hidden="true" />
                  {row.state === 'done' && row.verdict !== null
                    ? t(`routes.home.items.verdict.${row.verdict}`)
                    : t(`routes.home.items.state.${row.state}`)}
                </td>
                <td>
                  {row.item}
                  {row.attempt > 1 && (
                    <span className="item-row__attempt">
                      {t('routes.home.items.attempt', { n: String(row.attempt) })}
                    </span>
                  )}
                </td>
                <td>
                  {row.operator}
                  {row.when !== '' && (
                    <span className="item-row__when"> · {shortWhen(row.when)}</span>
                  )}
                </td>
                <td className="item-table__num item-row__when">
                  {row.minutes === null
                    ? '—'
                    : t('routes.home.items.minutes', { n: String(row.minutes) })}
                </td>
              </tr>
            ))}
          </tbody>
        ))}
      </table>

      {items.hasAmbiguousRows && (
        <p className="item-table__gap">{t('routes.home.items.gapNote')}</p>
      )}
    </div>
  );
}
