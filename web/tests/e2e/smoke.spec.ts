import { expect, test } from '@playwright/test';

test.describe('operator GUI — smoke каркаса', () => {
  test('головна сторінка рендериться українською', async ({ page }) => {
    await page.goto('/');

    await expect(page).toHaveTitle('UA Web Data Collector — operator GUI');
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(
      'UA Web Data Collector — operator GUI',
    );
    await expect(page.locator('html')).toHaveAttribute('lang', 'uk');
    // Дев'ять екранів §7.7 перелічені як план WP-11C.
    await expect(page.getByRole('listitem')).toHaveCount(9);
  });

  test('невідомий маршрут віддає SPA-сторінку 404, а не помилку сервера', async ({ page }) => {
    const response = await page.goto('/немає-такого-маршруту');

    expect(response?.status()).toBe(200);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Сторінку не знайдено');
  });

  test('застосунок нічого не пише у browser storage (§13)', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();

    const stored = await page.evaluate(() => ({
      local: window.localStorage.length,
      session: window.sessionStorage.length,
    }));
    expect(stored).toEqual({ local: 0, session: 0 });
  });
});
