import type { Locator, Page } from '@playwright/test';

/**
 * Scope a selector to the DESKTOP projection of a responsive DataTable.
 *
 * A table that opted into the §M7.2 column descriptor renders the same rows
 * twice — as a `<table>` and as a phone card list — and lets media queries
 * decide which one is visible. Playwright's strict mode sees both in the DOM,
 * so an unscoped `getByTestId` legitimately resolves to two elements even
 * though only one is ever visible. Scoping states which projection the
 * assertion is about, which is information the test should carry anyway.
 */
export function tableView(page: Page, tableTestId: string): Locator {
  return page.getByTestId(tableTestId);
}
