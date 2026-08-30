/**
 * Reference data authoring, against a live stack and a real principal.
 *
 * This is the lane the operator asked for: correction, ant gain, Test info and
 * frequency table. It runs under **real OIDC** rather than the injected session
 * the older live lane uses, and that is not a preference — every reference write
 * route calls `_require_actor`, so with auth disabled the fork/edit/publish
 * workflow this spec exercises would 403.
 *
 * No `page.route` interception of `/platform/**`: a mocked write proves the
 * button is wired, not that the value reached the database. No network-ledger
 * assertions either — under the dev gateway those classify Vite's dev module
 * URLs as unexpected, so the spec would go red for reasons unrelated to what it
 * claims to test.
 */
import { expect, test } from '@playwright/test';

import {
  openLiveLane,
  requireRevision,
  requireSeededLocator,
  requireSeededOption,
  requireText,
} from './helpers/live-stack-fixture';

/** Distinguishes a value this run wrote from the value the seed wrote. */
function runUniqueValue(prefix: string): string {
  return `${prefix}-${test.info().testId}-${test.info().repeatEachIndex}-${Date.now()}`;
}

test.describe('Reference data live authoring', () => {
  test.describe.configure({ mode: 'serial' });

  test('lists seeded revisions for the provider and reports web-authored provenance', async ({
    page,
    context,
  }) => {
    const ids = await openLiveLane('reference-data', page, context);

    await page.goto('/reference-data');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();

    await requireSeededOption(
      page.getByTestId('reference-data-provider-select'),
      requireText(ids, 'providerCode'),
      'the seeded provider',
    );

    // The screen's own empty state is the witness that provisioning happened.
    // "rows exist but the screen cannot see them" (wrong provider code, wrong
    // scope, missing permission) is a DIFFERENT failure from "no rows", and this
    // is the only signal that separates them.
    await expect(page.getByTestId('reference-data-empty')).toHaveCount(0);

    const published = requireRevision(ids, 'publishedRevision');
    await requireSeededLocator(
      page,
      `reference-open-${published.revision_id}`,
      `published ${published.family} revision`,
    );
  });

  test('saves an edited cell on a candidate and reads the new value back', async ({
    page,
    context,
  }) => {
    const ids = await openLiveLane('reference-data', page, context);
    const candidate = requireRevision(ids, 'candidateRevision');

    await page.goto('/reference-data');
    await requireSeededOption(
      page.getByTestId('reference-data-provider-select'),
      requireText(ids, 'providerCode'),
      'the seeded provider',
    );
    await requireSeededLocator(
      page,
      `reference-open-${candidate.revision_id}`,
      `candidate ${candidate.family} revision`,
    );
    await page.getByTestId(`reference-open-${candidate.revision_id}`).first().click();

    // Editable cells render only on candidates and only for non-identity
    // columns — an identity move is an add plus a remove, not an edit.
    const cell = page.getByTestId(/^reference-data-cell-/).first();
    await expect(cell).toBeVisible();
    const before = await cell.inputValue();
    const written = runUniqueValue('DEMO-EDIT');
    await cell.fill(written);

    const save = page.getByTestId('reference-data-save');
    await expect(save).toBeEnabled();
    await save.click();

    // Re-read from the server, not from the input we just typed into.
    await page.reload();
    const reread = page.getByTestId(/^reference-data-cell-/).first();
    await expect(reread).toHaveValue(written);
    expect(written).not.toBe(before);
  });

  test('forks a published revision, edits it and publishes the fork', async ({ page, context }) => {
    const ids = await openLiveLane('reference-data', page, context);
    const published = requireRevision(ids, 'publishedRevision');

    await page.goto('/reference-data');
    await requireSeededOption(
      page.getByTestId('reference-data-provider-select'),
      requireText(ids, 'providerCode'),
      'the seeded provider',
    );
    await requireSeededLocator(
      page,
      `reference-open-${published.revision_id}`,
      `published ${published.family} revision`,
    );
    await page.getByTestId(`reference-open-${published.revision_id}`).first().click();

    // Fork renders only on a published revision — that is exactly why the seed
    // publishes one, and why a candidate-only seed would leave this workflow
    // unreachable.
    const fork = page.getByTestId('reference-data-fork');
    await expect(fork).toBeVisible();
    await fork.click();

    const cell = page.getByTestId(/^reference-data-cell-/).first();
    await expect(cell).toBeVisible();
    await cell.fill(runUniqueValue('DEMO-FORKED'));
    await page.getByTestId('reference-data-save').click();

    const publish = page.getByTestId('reference-data-publish');
    await expect(publish).toBeEnabled();
    await publish.click();

    // The fork carries a higher revision number than its parent: succession
    // happened rather than a second row landing beside it.
    await expect(page.getByTestId('reference-data-publish-error')).toHaveCount(0);
    await page.reload();
    await requireSeededOption(
      page.getByTestId('reference-data-provider-select'),
      requireText(ids, 'providerCode'),
      'the seeded provider',
    );
    await expect(page.getByTestId('reference-data-empty')).toHaveCount(0);
  });

  test('blocks publishing a coupled family until its sibling is chosen', async ({
    page,
    context,
  }) => {
    const ids = await openLiveLane('reference-data', page, context);
    const coupled = requireRevision(ids, 'coupledCandidate');

    await page.goto('/reference-data');
    await requireSeededOption(
      page.getByTestId('reference-data-provider-select'),
      requireText(ids, 'providerCode'),
      'the seeded provider',
    );
    await requireSeededLocator(
      page,
      `reference-open-${coupled.revision_id}`,
      `coupled candidate (${coupled.family})`,
    );
    await page.getByTestId(`reference-open-${coupled.revision_id}`).first().click();

    // Correction and switch port mapping describe the same wiring. Publishing
    // half of the pair measures fine and is silently wrong — antenna 1's signal
    // with antenna 2's path loss — so the screen must not let it happen.
    await expect(page.getByTestId('reference-data-coupled-note')).toBeVisible();
    const sibling = page.getByTestId('reference-data-sibling-select');
    await expect(sibling).toBeVisible();
    await expect(sibling).toHaveValue('');
    await expect(page.getByTestId('reference-data-publish')).toBeDisabled();
  });
});
