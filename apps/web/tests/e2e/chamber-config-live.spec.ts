/**
 * Chamber instrument configuration, against a live stack and a real principal.
 *
 * Real OIDC rather than an injected session, even though `patch_equipment_config`
 * does not require an authenticated actor. The screen decides whether the
 * operator may write by reading `platform:chamber-config-write` off the **token
 * claims** — so asserting the write path with an injected token would be
 * asserting the fixture. "It happens to work under disabled auth" is exactly the
 * reasoning this lane's auth profile exists to forbid.
 *
 * No `page.route` interception of `/platform/**`.
 */
import { expect, test } from '@playwright/test';

import { openLiveLane, requireChamber, requireSeededOption } from './helpers/live-stack-fixture';

test.describe('Chamber equipment config live', () => {
  // Both tests write to the same chamber. Run them in order: concurrently they
  // race each other's saves, and the loser reads a value the other test wrote.
  test.describe.configure({ mode: 'serial' });

  test('renders descriptor-driven fields, reads back seeded values, and saves a new one', async ({
    page,
    context,
  }) => {
    const ids = await openLiveLane('chamber-config', page, context);
    const chamber = requireChamber(ids, 'chamberWithEquipmentConfig');

    await page.goto('/chambers');
    await expect(page.getByTestId('chambers-equipment')).toBeVisible();

    const picker = page.getByTestId('chambers-equipment-picker').locator('select');
    await expect(picker).toBeVisible();
    await requireSeededOption(picker, chamber.chamber_id, 'the seeded demo chamber');

    // The input set comes from the provider UI descriptor, so the fields render
    // even with no stored values — which is why "the screen looks populated" is
    // not evidence that anything was seeded. The VALUES are.
    const inputs = page.getByTestId('chambers-equipment-input');
    await expect(inputs.first()).toBeVisible();
    const fieldCount = await inputs.count();
    expect(fieldCount).toBeGreaterThan(0);

    // Fields render from the descriptor BEFORE the stored config arrives, so a
    // value read too early is empty and every later comparison is against the
    // wrong baseline. Wait for the value, then read it.
    const target = inputs.first();
    await expect(
      target,
      'the seeded equipment config should be readable — an empty field means the ' +
        'seed did not reach this chamber',
    ).not.toHaveValue('');
    const seeded = await target.inputValue();

    // A run-unique value, so a passing assertion cannot be satisfied by the
    // value that was already there.
    const written = `192.0.2.${(Date.now() % 200) + 20}`;
    expect(written).not.toBe(seeded);
    await target.fill(written);

    await expect(page.getByTestId('chambers-equipment-unsaved')).toBeVisible();
    const save = page.getByTestId('chambers-equipment-save');
    await expect(save).toBeEnabled();
    await save.click();

    // Re-read from the server rather than trusting the input we typed into.
    await page.reload();
    await page
      .getByTestId('chambers-equipment-picker')
      .locator('select')
      .selectOption(chamber.chamber_id);
    const reread = page.getByTestId('chambers-equipment-input').first();
    await expect(reread).toHaveValue(written);
    expect(await reread.inputValue()).not.toBe(seeded);
  });

  test('an untouched key keeps its stored value across a save', async ({ page, context }) => {
    const ids = await openLiveLane('chamber-config', page, context);
    const chamber = requireChamber(ids, 'chamberWithEquipmentConfig');

    await page.goto('/chambers');
    const picker = page.getByTestId('chambers-equipment-picker').locator('select');
    await requireSeededOption(picker, chamber.chamber_id, 'the seeded demo chamber');

    const inputs = page.getByTestId('chambers-equipment-input');
    await expect(inputs.first()).toBeVisible();
    // Asserted rather than skipped. A skip here would be a dead branch (the
    // descriptor exposes five endpoints today) that quietly becomes a silent
    // pass if it ever shrank — and a per-key merge is not observable at all
    // with one key, which is worth failing over rather than stepping around.
    expect(
      await inputs.count(),
      'per-key merge needs at least two fields to be observable',
    ).toBeGreaterThanOrEqual(2);

    // The PATCH is per key: an absent key is unchanged, a null key is deleted.
    // Sending every rendered field would turn it into a full replacement, and a
    // stale value in a field nobody touched would overwrite someone else's save.
    const untouched = inputs.nth(1);
    await expect(untouched).not.toHaveValue('');
    const preserved = await untouched.inputValue();
    await inputs.first().fill(`192.0.2.${(Date.now() % 200) + 20}`);
    await page.getByTestId('chambers-equipment-save').click();

    await page.reload();
    await page
      .getByTestId('chambers-equipment-picker')
      .locator('select')
      .selectOption(chamber.chamber_id);
    await expect(page.getByTestId('chambers-equipment-input').nth(1)).toHaveValue(preserved);
  });
});
