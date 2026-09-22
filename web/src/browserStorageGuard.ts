/**
 * Runtime-страховка проти запису у browser storage (§13, §7.7).
 *
 * ESLint-правила в `eslint.config.js` (`STORAGE_BAN`) — **синтаксичні**: вони бачать
 * `localStorage`, `window.localStorage`, `globalThis.sessionStorage` і `window['localStorage']`,
 * але не бачать присвоєння в проміжну змінну (`const w = window; w.localStorage.setItem(…)`,
 * `const g = globalThis as unknown as { localStorage: Storage }`). Це принципова межа
 * синтаксичного правила, а не недогляд конфігу — зафіксовано тестуванням WP-00 PR3 (M-1).
 *
 * Тому доступ блокується ще й у рантаймі: обидва сховища підмінюються геттером, який
 * логує і кидає. Обхід через alias помирає на першому ж зверненні — і в dev, і в prod.
 *
 * Чому не «просто не писати туди»: `localStorage`/`sessionStorage` читаються будь-яким JS на
 * origin (XSS миттєво віддає токен або персональні контакти), не мають `HttpOnly`/`SameSite`,
 * переживають вихід користувача і не інвалідуються сервером. Сесія живе у `HttpOnly`-cookie
 * від FastAPI BFF (WP-11A), тимчасовий стан — у пам'яті React/TanStack Query.
 *
 * Якщо WP-11C колись знадобиться браузерне сховище для НЕ-чутливого стану (напр. згорнута
 * панель), це робиться свідомою зміною тут + ADR, а не локальним `eslint-disable`.
 *
 * **Точні межі (код-рев'ю L-6 + security-рев'ю L-2, обидві перевірені у Chromium).** Guard —
 * anti-footgun проти *випадкового* використання, а не security-контроль. Він НЕ покриває:
 *
 * | вектор | стан |
 * |---|---|
 * | `localStorage`/`sessionStorage` у top-level realm, включно з alias-формами | заблоковано |
 * | `delete window.localStorage` | заблоковано (дескриптор лишається нашим) |
 * | **same-origin iframe** (`iframe.contentWindow.localStorage`) | **НЕ заблоковано** — guard ставиться лише на top-level realm, свіжий realm має незаймані акцесори |
 * | **IndexedDB, Cache API** | **не покрито взагалі** (саме туди пише `query-persist-client-idb`) |
 * | активний XSS | не покрито — він і так має повний доступ до origin |
 *
 * Тобто єдиний реальний захист від зловмисника — CSP і те, що токена в JS немає взагалі
 * (сесія у `HttpOnly`-cookie). Guard ловить власну необережність і залежності, що
 * звертаються до storage напряму з top-level realm. ESLint додатково забороняє `indexedDB`
 * і `caches` синтаксично.
 *
 * **Діагностика без шуму (код-рев'ю PR3, M-1).** `react-router` під час `initialize()`
 * безумовно читає `sessionStorage` (`restoreAppliedTransitions`, обгорнуте в `try/catch`),
 * тому кидок — штатна, очікувана подія на кожному завантаженні. Якби кожне звертання писало
 * `console.error`, повідомлення про порушення §13 з'являлось би там, де порушення немає, і
 * швидко б знецінилось. Тому: заборона (кидок) — завжди, а лог — `console.warn` і лише
 * ОДИН раз на сховище, з поясненням, що виклик міг прийти з бібліотеки.
 */

const STORAGE_KEYS = ['localStorage', 'sessionStorage'] as const;

/** Мітка вже встановленого guard-а — щоб повторний виклик не загортав guard у guard. */
const GUARD_MARK = 'collectorStorageGuard';

const MESSAGE =
  'Заборонено §13: browser storage не використовується в operator GUI ' +
  '(XSS-читання, немає HttpOnly/SameSite, не інвалідується сервером). ' +
  'Сесія — HttpOnly cookie від BFF; тимчасовий стан — у пам’яті (TanStack Query).';

/** Підказка до одноразового попередження: кидок сам по собі не означає баг у нашому коді. */
const WARN_HINT =
  'Звертання заблоковано. Якщо це не ваш код — виклик прийшов із залежності в бандлі ' +
  '(напр. react-router читає sessionStorage у try/catch); застосунок від цього не ламається.';

/**
 * Підміняє `window.localStorage` і `window.sessionStorage` геттерами, що кидають.
 * Ідемпотентна: повторний виклик нічого не змінює. Викликається у `src/main.tsx` до
 * першого рендеру.
 */
export function installBrowserStorageGuard(target: Window = window): void {
  for (const key of STORAGE_KEYS) {
    // `Reflect.get`, а не `descriptor.get`: пряме звертання до властивості дескриптора
    // ловить `@typescript-eslint/unbound-method` (тут метод нікуди не викликається).
    const descriptor = Object.getOwnPropertyDescriptor(target, key);
    const existing: unknown = descriptor && Reflect.get(descriptor, 'get');
    if (typeof existing === 'function' && GUARD_MARK in existing) {
      continue;
    }

    // Один warn на сховище за життя сторінки: заборона гучна там, де вона щось означає,
    // і не перетворюється на фоновий шум на кожному завантаженні (M-1).
    let warned = false;
    const guard = (): never => {
      if (!warned) {
        warned = true;
        console.warn(`${MESSAGE} ${WARN_HINT}`);
      }
      throw new TypeError(MESSAGE);
    };
    Object.defineProperty(guard, GUARD_MARK, { value: true });

    try {
      Object.defineProperty(target, key, {
        get: guard,
        set: guard,
        configurable: true,
      });
    } catch {
      // Браузер не дав перевизначити властивість — залишаємось зі статичною забороною
      // ESLint. Кидати тут не можна: це зламало б застосунок замість того, щоб захистити.
      console.error(`Не вдалося встановити guard на ${key}; діє лише ESLint-заборона (§13).`);
    }
  }
}
