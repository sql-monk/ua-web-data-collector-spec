import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

import { ESLint, Linter } from 'eslint';
import { beforeAll, describe, expect, it } from 'vitest';

/**
 * §13 ТЗ: access/refresh tokens не зберігаються у `localStorage`/`sessionStorage`; §7.7:
 * контакти не кешуються в browser storage. Заборона реалізована в `eslint.config.js`
 * (`STORAGE_BAN`) — цей тест доводить, що вона справді активна для коду застосунку і що
 * кожен обхідний синтаксис теж падає. Без нього правило може мовчки зникнути при рефакторингу.
 */

// Vitest запускається з кореня `web/` (там vite.config.ts) — це і є cwd для ESLint.
const projectRoot = process.cwd();
const STORAGE_RULES = [
  'no-restricted-globals',
  'no-restricted-properties',
  'no-restricted-syntax',
] as const;

let ruleConfig: Partial<Linter.RulesRecord>;

beforeAll(async () => {
  const eslint = new ESLint({ cwd: projectRoot });
  // Конфіг саме для файлу застосунку — не абстрактний, а той, за яким лінтиться `src/`.
  const config = (await eslint.calculateConfigForFile('src/main.tsx')) as Linter.Config;
  ruleConfig = Object.fromEntries(
    STORAGE_RULES.map((rule) => [rule, config.rules?.[rule]]),
  ) as Partial<Linter.RulesRecord>;
});

function lint(code: string): Linter.LintMessage[] {
  return new Linter().verify(code, { rules: ruleConfig });
}

// Файл запускає ESLint програмно з type-aware конфігом: на холодному кеші TS це десятки
// секунд. Таймаути підняті точково тут, а не глобально у vite.config.ts (код-рев'ю L-2).
describe('ESLint-заборона browser storage (§13)', { timeout: 120_000 }, () => {
  it('правила ввімкнені як error для src/', () => {
    for (const rule of STORAGE_RULES) {
      const entry = ruleConfig[rule];
      expect(entry, rule).toBeDefined();
      expect(Array.isArray(entry) ? entry[0] : entry, rule).toBe(2);
    }
  });

  it('повідомлення пояснює причину і посилається на §13', () => {
    const entry = ruleConfig['no-restricted-globals'] as Linter.RuleEntry<unknown[]>;
    const options = (entry as unknown[]).slice(1) as { name: string; message: string }[];
    // `indexedDB`/`caches` додані за security-рев'ю L-2: runtime-guard їх не покриває.
    expect(options.map((o) => o.name).sort()).toEqual([
      'caches',
      'indexedDB',
      'localStorage',
      'sessionStorage',
    ]);
    for (const option of options) {
      expect(option.message).toContain('§13');
    }
    const sessionStorageBan = options.find((o) => o.name === 'sessionStorage');
    expect(sessionStorageBan?.message).toContain('HttpOnly');
  });

  it.each([
    ['глобальний localStorage', "localStorage.setItem('access_token', t);"],
    ['глобальний sessionStorage', "sessionStorage.setItem('refresh_token', t);"],
    ['window.localStorage', "window.localStorage.setItem('access_token', t);"],
    ['window.sessionStorage', 'window.sessionStorage.clear();'],
    ['globalThis.localStorage', 'globalThis.localStorage.clear();'],
    ['self.sessionStorage', 'self.sessionStorage.clear();'],
    ['обчислений доступ', "window['localStorage'].clear();"],
  ])('падає на: %s', (_name, code) => {
    const messages = lint(code);
    expect(messages.length).toBeGreaterThan(0);
    expect(messages.every((m) => m.severity === 2)).toBe(true);
    expect(messages.some((m) => m.message.includes('§13'))).toBe(true);
  });

  it('дозволений код без browser storage лишається чистим', () => {
    expect(lint('const cache = new Map(); cache.set("k", 1);')).toEqual([]);
  });

  it('реальні вихідники GUI не використовують browser storage', async () => {
    const eslint = new ESLint({ cwd: projectRoot });
    const results = await eslint.lintFiles(['src/**/*.{ts,tsx}']);
    const violations = results
      .flatMap((r) => r.messages.map((m) => ({ file: r.filePath, ruleId: m.ruleId })))
      .filter((m) => (STORAGE_RULES as readonly string[]).includes(m.ruleId ?? ''));
    expect(violations).toEqual([]);
  });

  /**
   * Відома межа (зафіксована тестувальником WP-00 PR3, знахідка M-1): правила
   * `no-restricted-globals`/`no-restricted-properties`/`no-restricted-syntax` синтаксичні —
   * вони бачать лише `localStorage`, `window.localStorage`, `window['localStorage']`.
   * Присвоєння в проміжну змінну (`const w = window; w.localStorage…`,
   * `globalThis as unknown as {localStorage}`) ESLint не ловить і зловити не може без
   * type-aware правила на тип `Storage`.
   *
   * Тому статична заборона не є єдиним бар'єром: `src/browserStorageGuard.ts` підміняє
   * обидва сховища геттером, що кидає, а `src/main.tsx` ставить його до першого рендеру.
   * Ці тести не дають прибрати runtime-страховку непомітно — ні модуль, ні його
   * встановлення, ні E2E-перевірку, що alias-обхід у реальному браузері падає.
   */
  it('runtime-страховка існує і підключена у точці входу', async () => {
    // Навмисно НЕ перевіряємо форму реалізації guard-а (код-рев'ю L-4) — його поведінку
    // покриває browser-storage-guard.test.ts, а обхід у браузері — E2E. Тут лише факт, що
    // модуль існує і його викликає `main.tsx`: без цього alias-форми знову проходять німо.
    const main = await readFile(join(projectRoot, 'src', 'main.tsx'), 'utf8');

    await expect(
      readFile(join(projectRoot, 'src', 'browserStorageGuard.ts'), 'utf8'),
    ).resolves.toBeTruthy();
    expect(main).toMatch(/installBrowserStorageGuard/);
  });
});
