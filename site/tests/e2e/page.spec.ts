import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

/**
 * One page, four Playwright projects (desktop 1440, tablet 768, mobile 375,
 * reduced-motion 1440 -- see playwright.config.ts). Screenshots are taken
 * as artifacts under Playwright's own `test-results/` output (already
 * gitignored) rather than committed baseline images: this suite asserts
 * behavior (axe, layout, keyboard, motion), it does not diff pixels.
 */

test('captures a full-page screenshot', async ({ page }, testInfo) => {
  await page.goto('/');
  await page.screenshot({ path: testInfo.outputPath('full-page.png'), fullPage: true });
});

test('has zero automated accessibility violations', async ({ page }) => {
  await page.goto('/');
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
});

test('has no horizontal scroll', async ({ page }) => {
  await page.goto('/');
  const fitsViewport = await page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth,
  );
  expect(fitsViewport).toBe(true);
});

test('keyboard: tabs through scenario tabs and player controls', async ({ page }) => {
  await page.goto('/');

  const tabs = page.locator('[data-tabs] [role="tab"]');
  await expect(tabs.first()).toBeVisible();

  await tabs.first().focus();
  await expect(tabs.first()).toHaveAttribute('aria-selected', 'true');

  // ArrowRight moves the roving tabindex to the next scenario tab and
  // mounts its panel (initScenarioTabs in islands/replay-player.ts).
  await page.keyboard.press('ArrowRight');
  await expect(tabs.nth(1)).toHaveAttribute('aria-selected', 'true');
  await expect(tabs.nth(1)).toBeFocused();

  // From the now-selected tab, Tab must walk into the newly visible
  // panel's controls in document order: play, pause, step, restart.
  await page.keyboard.press('Tab');
  await expect(page.locator(':focus')).toHaveAttribute('data-play', '');
  await page.keyboard.press('Tab');
  await expect(page.locator(':focus')).toHaveAttribute('data-pause', '');
  await page.keyboard.press('Tab');
  await expect(page.locator(':focus')).toHaveAttribute('data-step', '');
  await page.keyboard.press('Tab');
  await expect(page.locator(':focus')).toHaveAttribute('data-restart', '');
});

test('reduced motion: hero transcript renders its final line immediately', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'reduced-motion', 'only meaningful under reduced motion');

  await page.goto('/');

  // Typewriter.start() takes the synchronous renderFinal() path under
  // reduced motion (no per-character timers), so the transcript's last
  // line is already in the DOM well inside a 200ms bound instead of
  // arriving after several seconds of simulated typing.
  const lastLine = page.locator('.term > .line').last();
  await expect(lastLine).toBeVisible({ timeout: 200 });
  await expect(lastLine).not.toBeEmpty();
});
