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
 * логує і кидає. Обхід через alias помирає на першому ж зверненні — і в dev, і в prod,
 * у власному коді й у будь-якій залежності, що потрапила в bundle.
 *
 * Чому не «просто не писати туди»: `localStorage`/`sessionStorage` читаються будь-яким JS на
 * origin (XSS миттєво віддає токен або персональні контакти), не мають `HttpOnly`/`SameSite`,
 * переживають вихід користувача і не інвалідуються сервером. Сесія живе у `HttpOnly`-cookie
 * від FastAPI BFF (WP-11A), тимчасовий стан — у пам'яті React/TanStack Query.
 *
 * Якщо WP-11C колись знадобиться браузерне сховище для НЕ-чутливого стану (напр. згорнута
 * панель), це робиться свідомою зміною тут + ADR, а не локальним `eslint-disable`.
 */

const STORAGE_KEYS = ['localStorage', 'sessionStorage'] as const;

/** Мітка вже встановленого guard-а — щоб повторний виклик не загортав guard у guard. */
const GUARD_MARK = 'collectorStorageGuard';

const MESSAGE =
  'Заборонено §13: browser storage не використовується в operator GUI ' +
  '(XSS-читання, немає HttpOnly/SameSite, не інвалідується сервером). ' +
  'Сесія — HttpOnly cookie від BFF; тимчасовий стан — у пам’яті (TanStack Query).';

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

    const guard = (): never => {
      // Помилку видно і в консолі, і в E2E: мовчазне падіння сховища гірше за гучне.
      console.error(MESSAGE);
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
