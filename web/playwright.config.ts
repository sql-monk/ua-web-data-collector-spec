import { defineConfig, devices } from '@playwright/test';

/**
 * E2E каркаса (§16.2 `npm run test:e2e`): один smoke-тест проти `vite preview` — статики, яку
 * віддає nginx у контейнері. E2E проти повного Docker stack (з API, OIDC і SSE) — WP-11C.
 */
const PORT = 4173;

export default defineConfig({
  testDir: './tests/e2e',
  // 30 с за замовчуванням замало для першого воркера на холодному/навантаженому хості
  // (старт браузера + перший запит до щойно піднятого preview) — спостерігалось локально
  // одразу після `npm ci`. Тест-логіка тут секундна, тому запас нічого не маскує.
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : [['list']],
  use: {
    baseURL: `http://127.0.0.1:${String(PORT)}`,
    trace: 'on-first-retry',
    locale: 'uk-UA',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    // `preview` віддає готовий bundle, тому збірка обов'язкова — виконуємо її тут, щоб
    // `npm run test:e2e` працював і окремо від ланцюжка §16.2.
    command: 'npm run build && npm run preview',
    url: `http://127.0.0.1:${String(PORT)}/`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
