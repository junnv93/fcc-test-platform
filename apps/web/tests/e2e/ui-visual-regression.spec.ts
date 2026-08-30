import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';

import { injectAuthenticatedSession, TEST_ADMIN_PERMISSIONS } from './helpers/auth-fixture';
import { messageAt } from './helpers/locale-messages';
import {
  assertVisualRouteReady,
  installVisualFixture,
  wave1VisualSnapshotName,
  visualSnapshotName,
  WAVE_1_VISUAL_ROUTE_DEFINITIONS,
  VISUAL_ROUTE_DEFINITIONS,
  type VisualDensity,
  type VisualLocale,
  type VisualTheme,
} from './helpers/visual-fixture';

const THEMES: readonly VisualTheme[] = ['light', 'dark'];
const DENSITIES: readonly VisualDensity[] = ['comfortable', 'compact'];
const WIDTHS = [1280, 1440] as const;
const STYLE_PATH = fileURLToPath(new URL('./ui-visual-regression.css', import.meta.url));
const VISUAL_PERMISSIONS = [
  ...TEST_ADMIN_PERMISSIONS,
  'test_plan:read',
  'test_plan:author',
] as const;
const WAVE_1_LOCALES: readonly VisualLocale[] = ['ko', 'en'];
/**
 * Deterministic production-preview matrix: 7 routes × 2 themes × 2 densities
 * × 2 desktop widths = 56 named PNGs. Mobile/tablet structure is covered by
 * responsive-layout.spec.ts; these goldens are deliberately the desktop
 * information-density contract.
 */
test.describe('FCC visual regression matrix', () => {
  // `default`, not `serial` (e2e-visual-lane-repair, 2026-08-10). Both modes run
  // the matrix in declaration order inside one worker — that part is unchanged,
  // and it is the part screenshot determinism actually depends on. What `serial`
  // adds is that the FIRST failure marks every later test in the group as "did
  // not run". Measured: one stale golden (`home / light / comfortable / 1280`,
  // 947px → 1030px after two nav entries landed) blacked out **110** of the 112
  // matrix cases in a single run.
  //
  // The cases it hid are not only pixel comparisons. Each one also asserts no
  // pageerror, no console.error, no unexpected external request, no horizontal
  // overflow, and exactly one `h1` — checks that are independent of any golden
  // and that a pixel drift has no business suppressing. `serial` is for tests
  // that share state; these share none (each builds its own fixture, context,
  // viewport, and storage).
  test.describe.configure({ mode: 'default' });

  for (const route of VISUAL_ROUTE_DEFINITIONS) {
    for (const theme of THEMES) {
      for (const density of DENSITIES) {
        for (const width of WIDTHS) {
          test(`${route.key} / ${theme} / ${density} / ${width}`, async ({ context, page }) => {
            const fixture = await installVisualFixture(context);
            const pageErrors: string[] = [];
            const consoleErrors: string[] = [];
            page.on('pageerror', (error) => pageErrors.push(error.message));
            page.on('console', (message) => {
              if (message.type() === 'error') consoleErrors.push(message.text());
            });

            await page.setViewportSize({ width, height: 900 });
            await page.emulateMedia({ colorScheme: theme });
            await page.addInitScript(
              ({ selectedTheme, selectedDensity }) => {
                window.localStorage.setItem('fcc-theme', selectedTheme);
                window.localStorage.setItem('fcc-density', selectedDensity);
                window.localStorage.setItem('fcc-locale', 'ko');
              },
              { selectedTheme: theme, selectedDensity: density },
            );
            await injectAuthenticatedSession(page, { permissions: VISUAL_PERMISSIONS });
            expect(
              await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches),
              `${route.key}: reducedMotion must be configured on BrowserContext before navigation`,
            ).toBe(true);

            await page.goto(route.path, { waitUntil: 'domcontentloaded' });
            await assertVisualRouteReady(page, route);

            expect(pageErrors, `${route.key}: pageerror`).toEqual([]);
            expect(consoleErrors, `${route.key}: console.error`).toEqual([]);
            expect(fixture.pageErrors, `${route.key}: fixture pageerror`).toEqual([]);
            expect(fixture.unexpectedConsoleErrors, `${route.key}: fixture console.error`).toEqual(
              [],
            );
            expect(fixture.unexpectedRequests, `${route.key}: unexpected request`).toEqual([]);
            expect(
              await page.evaluate(
                () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
              ),
              `${route.key}: horizontal overflow`,
            ).toBe(true);
            await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);

            await expect(page).toHaveScreenshot(
              visualSnapshotName(route.key, theme, density, width),
              {
                animations: 'disabled',
                caret: 'hide',
                fullPage: true,
                scale: 'css',
                stylePath: STYLE_PATH,
              },
            );
          });
        }
      }
    }
  }
});

test.describe('Wave 1 bilingual canonical visual evidence', () => {
  for (const route of WAVE_1_VISUAL_ROUTE_DEFINITIONS) {
    for (const locale of WAVE_1_LOCALES) {
      test(`${route.key} / ${locale} / light / comfortable / 1440`, async ({ context, page }) => {
        const fixture = await installVisualFixture(context);
        const pageErrors: string[] = [];
        const consoleErrors: string[] = [];
        page.on('pageerror', (error) => pageErrors.push(error.message));
        page.on('console', (message) => {
          if (message.type() === 'error') consoleErrors.push(message.text());
        });

        await page.setViewportSize({ width: 1440, height: 900 });
        await page.emulateMedia({ colorScheme: 'light' });
        await page.addInitScript((selectedLocale) => {
          window.localStorage.setItem('fcc-theme', 'light');
          window.localStorage.setItem('fcc-density', 'comfortable');
          window.localStorage.setItem('fcc-locale', selectedLocale);
        }, locale);
        await injectAuthenticatedSession(page, { permissions: VISUAL_PERMISSIONS });
        expect(
          await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches),
          `${route.key}/${locale}: reducedMotion must be configured on BrowserContext before navigation`,
        ).toBe(true);

        await page.goto(route.path, { waitUntil: 'domcontentloaded' });
        await assertVisualRouteReady(page, route);
        await expect(page.locator('html')).toHaveAttribute('lang', locale);
        await expect(
          page.getByRole('heading', {
            level: 1,
            name: messageAt(route.titleKey, locale),
            exact: true,
          }),
        ).toBeVisible();

        expect(pageErrors, `${route.key}/${locale}: pageerror`).toEqual([]);
        expect(consoleErrors, `${route.key}/${locale}: console.error`).toEqual([]);
        expect(fixture.pageErrors, `${route.key}/${locale}: fixture pageerror`).toEqual([]);
        expect(
          fixture.unexpectedConsoleErrors,
          `${route.key}/${locale}: fixture console.error`,
        ).toEqual([]);
        expect(fixture.unexpectedRequests, `${route.key}/${locale}: unexpected request`).toEqual(
          [],
        );
        expect(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
          ),
          `${route.key}/${locale}: horizontal overflow`,
        ).toBe(true);

        if (route.key === 'inventory') {
          const sample = page.getByTestId('inventory-sample-S-001');
          await expect(page.getByTestId('inventory-sample-list')).toBeVisible();
          await expect(sample).toContainText('S-001');
          await expect(sample).toContainText('RF');
        } else if (route.key === 'test-plans') {
          const filePrefix = 'test-plans-import';
          const fileLabelKey = 'routes.testPlans.sectionImport';
          const fileInput = page.getByTestId(`${filePrefix}-file`);
          const fileStatus = page.getByTestId(`${filePrefix}-file-status`);
          const filePicker = page.getByTestId(`${filePrefix}-file-picker`);
          await expect(fileInput).toHaveClass('sr-only');
          await expect(filePicker).toBeVisible();
          await expect(fileInput).toHaveAccessibleName(messageAt(fileLabelKey, locale));
          await expect(filePicker).toHaveAccessibleName(messageAt(fileLabelKey, locale));
          await expect(fileInput).toHaveAttribute('aria-describedby', `${filePrefix}-file-help`);
          await expect(filePicker).toHaveAttribute('data-file-state', 'empty');
          await expect(fileStatus).toHaveText(messageAt(fileLabelKey, locale));
          await expect(fileStatus).not.toContainText('Choose File');
        }

        await expect(page).toHaveScreenshot(
          wave1VisualSnapshotName(route.key, locale, 'light', 'comfortable', 1440),
          {
            animations: 'disabled',
            caret: 'hide',
            fullPage: true,
            scale: 'css',
            stylePath: STYLE_PATH,
          },
        );
      });
    }
  }
});
