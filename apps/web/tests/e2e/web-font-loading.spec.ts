import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page, type Route } from '@playwright/test';

import { injectAuthenticatedSession, TEST_OPERATOR_PERMISSIONS } from './helpers/auth-fixture';

const PROJECTS_GLOB = '**/platform/projects*';
const FONT_FAMILY = 'Noto Sans KR Variable';
const FONT_EVIDENCE_DIR = process.env['FONT_EVIDENCE_DIR'];
const MAX_REPRESENTATIVE_FONT_REQUESTS = 16;
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

test.use({ viewport: { width: 1440, height: 900 } });

async function openMyProjects(page: Page): Promise<void> {
  await page.route(PROJECTS_GLOB, async (route: Route) => {
    if (route.request().method() !== 'GET') {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify([
        {
          project_id: 'font-evidence-project',
          project_code: 'SM-S921U',
          model_name: '한국어 폰트 검증용 모델',
          status: 'active',
          sample_count: 2,
          management_number: '4792232056',
          fcc_id: null,
          applicant_name: '폰트 검증 신청자',
          applicant_address: null,
          manufacturer: null,
          fcc_grantee_code: null,
          eut_description: null,
          test_standard: null,
        },
      ]),
    });
  });
  await injectAuthenticatedSession(page, { permissions: TEST_OPERATOR_PERMISSIONS });
  await page.goto('/my-projects');
  await expect(page.getByRole('heading', { name: '내 프로젝트', level: 1 })).toBeVisible();
  await expect(page.getByTestId('project-card')).toBeVisible();
}

test.describe('self-hosted Korean web font', () => {
  test('loads the local face and applies it globally on the Korean my-projects screen', async ({
    page,
  }) => {
    const fontResponses: string[] = [];
    page.on('response', (response) => {
      if (response.request().resourceType() === 'font') fontResponses.push(response.url());
    });

    await openMyProjects(page);

    const evidence = await page.evaluate(async (fontFamily) => {
      const sample = '내 프로젝트 한국어 폰트 검증';
      await document.fonts.load(`400 14px "${fontFamily}"`, sample);
      await document.fonts.load(`600 26px "${fontFamily}"`, sample);
      await document.fonts.ready;

      const title = document.querySelector('.page-header__title');
      const faces = Array.from(document.fonts).filter((face) =>
        face.family.replaceAll('"', '').includes(fontFamily),
      );

      return {
        bodyFamily: getComputedStyle(document.body).fontFamily,
        fontStatus: document.fonts.status,
        loadedFaceCount: faces.filter((face) => face.status === 'loaded').length,
        titleFamily: title === null ? null : getComputedStyle(title).fontFamily,
        titleFontSize: title === null ? null : getComputedStyle(title).fontSize,
        titleFontWeight: title === null ? null : getComputedStyle(title).fontWeight,
        transferredFontBytes: performance
          .getEntriesByType('resource')
          .filter(
            (entry): entry is PerformanceResourceTiming =>
              entry instanceof PerformanceResourceTiming && entry.name.endsWith('.woff2'),
          )
          .reduce((total, entry) => total + entry.transferSize, 0),
      };
    }, FONT_FAMILY);

    expect(evidence.fontStatus).toBe('loaded');
    expect(evidence.loadedFaceCount).toBeGreaterThan(0);
    expect(evidence.bodyFamily).toContain(FONT_FAMILY);
    expect(evidence.titleFamily).toContain(FONT_FAMILY);
    expect(evidence.titleFontSize).toBe('26px');
    expect(evidence.titleFontWeight).toBe('600');
    expect(evidence.transferredFontBytes).toBeGreaterThan(0);
    await expect.poll(() => fontResponses.length).toBeGreaterThan(0);
    expect(fontResponses.length).toBeLessThanOrEqual(MAX_REPRESENTATIVE_FONT_REQUESTS);
    for (const fontUrl of fontResponses) {
      expect(new URL(fontUrl).origin).toBe(new URL(page.url()).origin);
      expect(fontUrl).toContain('.woff2');
    }

    if (FONT_EVIDENCE_DIR !== undefined) {
      const viewport = page.viewportSize();
      if (viewport === null) throw new Error('The font evidence run requires a fixed viewport.');

      await mkdir(FONT_EVIDENCE_DIR, { recursive: true });
      const screenshot = await page.screenshot({
        path: join(FONT_EVIDENCE_DIR, 'my-projects-ko-1440x900.png'),
      });
      // PNG IHDR stores its pixel width and height at byte offsets 16 and 20.
      // Keep the actual image dimensions next to the declared viewport so a
      // mislabeled capture cannot satisfy the evidence requirement.
      const screenshotDimensions = {
        width: screenshot.readUInt32BE(16),
        height: screenshot.readUInt32BE(20),
      };
      await writeFile(
        join(FONT_EVIDENCE_DIR, 'font-loading.json'),
        `${JSON.stringify({ ...evidence, fontResponses, screenshotDimensions, viewport }, null, 2)}\n`,
        'utf-8',
      );
    }
  });

  test('keeps the representative control matrix accessible across theme and density', async ({
    page,
  }) => {
    await openMyProjects(page);
    const matrix = [
      { density: 'comfortable', scheme: 'light', minHeight: 36 },
      { density: 'compact', scheme: 'light', minHeight: 28 },
      { density: 'comfortable', scheme: 'dark', minHeight: 36 },
      { density: 'compact', scheme: 'dark', minHeight: 28 },
    ] as const;

    for (const entry of matrix) {
      await page.emulateMedia({ colorScheme: entry.scheme });
      await page.evaluate((density) => {
        delete document.documentElement.dataset.theme;
        if (density === 'compact') document.documentElement.dataset.density = density;
        else delete document.documentElement.dataset.density;
      }, entry.density);

      // `type="search"` exposes the ARIA `searchbox` role, not `textbox`.
      // Wait for the route-owned control explicitly so this gate cannot race
      // the lazy route chunk after the project card has mounted.
      const search = page.getByRole('searchbox', { name: '검색' });
      await expect(search).toBeVisible({ timeout: 15_000 });
      await search.focus();
      await expect(search).toBeFocused();
      const styles = await search.evaluate((element) => {
        const style = getComputedStyle(element);
        return { height: parseFloat(style.height), outlineStyle: style.outlineStyle };
      });
      expect(styles.height).toBeGreaterThanOrEqual(entry.minHeight);
      expect(styles.outlineStyle).not.toBe('none');
      // The blocking accessibility contract is zero critical/serious axe
      // violations. Moderate best-practice findings are owned by the full
      // route sweep in a11y.spec.ts; keeping this font gate scoped prevents a
      // non-font landmark advisory from making the representative font command
      // flaky while still failing on every blocking WCAG violation.
      const axeResults = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
      expect(
        axeResults.violations.filter(
          (violation) => violation.impact === 'critical' || violation.impact === 'serious',
        ),
      ).toEqual([]);

      if (FONT_EVIDENCE_DIR !== undefined) {
        await page.screenshot({
          path: join(
            FONT_EVIDENCE_DIR,
            `my-projects-${entry.scheme}-${entry.density}-1440x900.png`,
          ),
        });
      }
    }
  });
});
