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

    // Читаємо сховище повз сторінку (`storageState` бере його з контексту браузера), бо
    // всередині сторінки геттер заблоковано guard-ом — див. наступний тест.
    const state = await page.context().storageState();
    const origin = state.origins.find((o) => o.origin.includes('127.0.0.1'));
    expect(origin?.localStorage ?? []).toEqual([]);
  });

  test('guard блокує обхід ESLint через alias (§13, M-1)', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();

    // Саме ті форми, які синтаксичні ESLint-правила зловити не можуть: доступ через
    // проміжну змінну і через змінну-ключ.
    const attempts = await page.evaluate(() => {
      type StorageKey = 'localStorage' | 'sessionStorage';
      const read = (host: object, key: StorageKey): Storage =>
        (host as Record<StorageKey, Storage>)[key];
      const probe = (run: () => void): string => {
        try {
          run();
          return 'NOT BLOCKED';
        } catch (error) {
          return error instanceof Error ? error.message : String(error);
        }
      };

      return {
        aliasLocal: probe(() => {
          const alias = window;
          read(alias, 'localStorage').setItem('access_token', 'x');
        }),
        aliasSession: probe(() => {
          const alias = window;
          read(alias, 'sessionStorage').setItem('refresh_token', 'x');
        }),
        globalRef: probe(() => {
          read(globalThis, 'localStorage').setItem('access_token', 'x');
        }),
      };
    });

    for (const [form, message] of Object.entries(attempts)) {
      expect(message, form).not.toBe('NOT BLOCKED');
      expect(message, form).toContain('§13');
    }
  });
});
