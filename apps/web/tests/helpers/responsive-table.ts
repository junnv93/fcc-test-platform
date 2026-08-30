import { screen, within } from '@testing-library/react';

import type { BoundFunctions, queries } from '@testing-library/dom';

/**
 * Scope a query to the DESKTOP projection of a responsive DataTable.
 *
 * A table that opted into the §M7.2 column descriptor renders two views of the
 * same rows — the `<table>` and a phone card list — and lets media queries pick
 * one. jsdom has no layout engine, so both are "present" and an unscoped
 * `screen.getByText` legitimately finds two matches. Scoping to the table is
 * the correct fix (not deduplicating the markup): the duplication is the
 * contract, and at any real viewport `display: none` keeps exactly one view in
 * the accessibility tree.
 *
 * Use {@link cardView} for the phone projection.
 */
export function tableView(tableTestId: string): BoundFunctions<typeof queries> {
  return within(screen.getByTestId(tableTestId));
}

/** Scope a query to the phone card projection of a responsive DataTable. */
export function cardView(): BoundFunctions<typeof queries> {
  return within(screen.getByTestId('data-table-cards'));
}
