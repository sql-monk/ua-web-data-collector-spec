// ESLint 9 flat config для operator GUI (§8, §13).
//
// Ключове правило безпеки — заборона browser storage (див. STORAGE_BAN нижче).
// Правило перевіряється тестом tests/unit/eslint-no-browser-storage.test.ts: він запускає
// ESLint програмно на зразку коду і вимагає помилку. Без такого тесту правило може мовчки
// зникнути при рефакторингу конфігу.
import js from '@eslint/js';
import prettier from 'eslint-config-prettier';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import globals from 'globals';
import tseslint from 'typescript-eslint';

/**
 * §13 ТЗ: «GUI використовує same-origin BFF session, CSRF token і restrictive CSP;
 * access/refresh tokens не зберігаються в `localStorage/sessionStorage`». §7.7: «контакти не
 * кешуються в browser storage».
 *
 * Причина заборони: `localStorage`/`sessionStorage` читаються будь-яким JS на origin, тому
 * XSS негайно віддає токен або персональні контакти; вони не мають `HttpOnly`/`SameSite`,
 * переживають вихід користувача і не інвалідуються сервером. Сесія живе у `HttpOnly`
 * cookie, яку ставить FastAPI BFF (WP-11A); ефемерний стан — у пам'яті React/TanStack Query.
 *
 * Знімати заборону не можна локально (`eslint-disable`) — лише зміною цього файлу в PR з
 * поясненням, бо це вимога §13, а не стильова преференція.
 *
 * **Межа цих правил (знахідка M-1, gate 2 WP-00 PR3).** Усі три правила синтаксичні: вони
 * бачать `localStorage`, `window.localStorage`, `globalThis.sessionStorage`,
 * `window['localStorage']` і `self.localStorage` — тобто форми, де ім'я сховища присутнє в
 * коді буквально. Присвоєння в проміжну змінну ESLint не ловить і зловити не може без
 * type-aware правила на тип `Storage`:
 *
 *     const w = window;                                   // ← lint чистий
 *     w.localStorage.setItem('access_token', token);      // ← lint чистий
 *
 * Тому статична заборона — не єдиний бар'єр. `src/browserStorageGuard.ts` підміняє обидва
 * сховища геттером, що кидає, і `src/main.tsx` ставить його до першого рендеру: alias-обхід
 * помирає у рантаймі. Перевірено `tests/unit/browser-storage-guard.test.ts` і E2E-тестом
 * «guard блокує обхід ESLint через alias». Прибирати guard разом із «зайвим» кодом не можна —
 * тоді alias-форми знову проходять непоміченими.
 */
const STORAGE_BAN =
  'Заборонено §13: токени і контакти не зберігаються у localStorage/sessionStorage ' +
  '(XSS-читання, немає HttpOnly/SameSite, не інвалідуються сервером). ' +
  'Сесія — HttpOnly cookie від FastAPI BFF; тимчасовий стан — у пам’яті (TanStack Query).';

export default tseslint.config(
  {
    ignores: [
      'dist/**',
      'coverage/**',
      'playwright-report/**',
      'test-results/**',
      'node_modules/**',
    ],
  },

  js.configs.recommended,
  tseslint.configs.strictTypeChecked,
  tseslint.configs.stylisticTypeChecked,

  {
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },

  // --- застосунок і unit-тести: browser globals, React ---------------------------------
  {
    files: ['src/**/*.{ts,tsx}', 'tests/unit/**/*.{ts,tsx}'],
    languageOptions: {
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],

      // §13 — browser storage.
      'no-restricted-globals': [
        'error',
        { name: 'localStorage', message: STORAGE_BAN },
        { name: 'sessionStorage', message: STORAGE_BAN },
      ],
      'no-restricted-properties': [
        'error',
        { object: 'window', property: 'localStorage', message: STORAGE_BAN },
        { object: 'window', property: 'sessionStorage', message: STORAGE_BAN },
        { object: 'globalThis', property: 'localStorage', message: STORAGE_BAN },
        { object: 'globalThis', property: 'sessionStorage', message: STORAGE_BAN },
      ],
      // `window['localStorage']`/`self.localStorage` обходять правила вище — синтаксис явно.
      'no-restricted-syntax': [
        'error',
        {
          selector: 'MemberExpression[computed=true] > Literal[value=/^(local|session)Storage$/]',
          message: STORAGE_BAN,
        },
        {
          selector:
            'MemberExpression[object.name="self"][property.name=/^(local|session)Storage$/]',
          message: STORAGE_BAN,
        },
      ],
    },
  },

  // --- Node-частина: конфіги і Playwright ------------------------------------------------
  {
    files: ['*.config.{ts,js}', 'tests/e2e/**/*.ts'],
    languageOptions: {
      globals: globals.node,
    },
  },

  // Конфіг ESLint сам не входить у tsconfig-проєкти (він .js) — без type-aware правил.
  {
    files: ['eslint.config.js'],
    extends: [tseslint.configs.disableTypeChecked],
    languageOptions: { globals: globals.node },
  },

  // Prettier вимикає стильові правила — форматування перевіряє `prettier --check`.
  prettier,
);
