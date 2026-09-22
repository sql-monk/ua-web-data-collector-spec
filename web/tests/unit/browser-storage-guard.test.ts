import { describe, expect, it, vi } from 'vitest';

import { installBrowserStorageGuard } from '~/browserStorageGuard';

/**
 * Межа §13 (знахідка M-1 gate 2): статичні ESLint-правила не ловлять alias-обхід
 * (`const w = window; w.localStorage…`). Guard закриває саме цей розрив у рантаймі —
 * тому його поведінка перевіряється окремо від конфігу лінтера.
 *
 * Звертання до сховищ тут — через змінну-ключ (`read(target, key)`), а не літерал: інакше
 * цей файл падав би на власній ESLint-забороні, яку він і перевіряє.
 */
type StorageKey = 'localStorage' | 'sessionStorage';

const STORAGE_KEYS: readonly StorageKey[] = ['localStorage', 'sessionStorage'];

function read(target: Window, key: StorageKey): Storage {
  return (target as unknown as Record<StorageKey, Storage>)[key];
}

function write(target: Window, key: StorageKey, value: Storage): void {
  (target as unknown as Record<StorageKey, Storage>)[key] = value;
}

/** Геттер властивості як непрозоре значення (пряме `descriptor.get` ловить unbound-method). */
function getterOf(target: Window, key: StorageKey): unknown {
  const descriptor = Object.getOwnPropertyDescriptor(target, key);
  return descriptor && Reflect.get(descriptor, 'get');
}

/** Мінімальний «window» з обома сховищами, які можна перевизначити. */
function freshWindow(): Window {
  const fake = {} as Window;
  for (const key of STORAGE_KEYS) {
    Object.defineProperty(fake, key, {
      value: { setItem: () => undefined, length: 0 } as unknown as Storage,
      configurable: true,
      writable: true,
    });
  }
  vi.spyOn(console, 'warn').mockImplementation(() => undefined);
  return fake;
}

describe('installBrowserStorageGuard (§13)', () => {
  it.each(STORAGE_KEYS)('доступ до %s кидає TypeError з поясненням', (key) => {
    const target = freshWindow();
    installBrowserStorageGuard(target);

    expect(() => read(target, key)).toThrow(TypeError);
    expect(() => read(target, key)).toThrow(/§13/);
  });

  it.each(STORAGE_KEYS)('запис у %s теж заблоковано (setter)', (key) => {
    const target = freshWindow();
    installBrowserStorageGuard(target);

    expect(() => {
      write(target, key, {} as Storage);
    }).toThrow(/§13/);
  });

  it('блокує alias-обхід, який синтаксичне правило ESLint зловити не може', () => {
    const target = freshWindow();
    installBrowserStorageGuard(target);

    const alias = target;
    expect(() => {
      read(alias, 'localStorage').setItem('access_token', 'x');
    }).toThrow(/§13/);
  });

  it('попереджає в консоль один раз на сховище, а не на кожне звертання (M-1)', () => {
    const target = freshWindow();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    installBrowserStorageGuard(target);

    for (let i = 0; i < 5; i += 1) {
      expect(() => read(target, 'localStorage')).toThrow(/§13/);
    }
    // Кидок — щоразу (заборона), warn — один (react-router читає sessionStorage на старті).
    expect(warn.mock.calls.filter((call) => String(call[0]).includes('§13'))).toHaveLength(1);
  });

  it('ідемпотентна: повторний виклик не загортає guard у guard', () => {
    const target = freshWindow();
    installBrowserStorageGuard(target);
    const first = getterOf(target, 'localStorage');
    installBrowserStorageGuard(target);
    const second = getterOf(target, 'localStorage');

    expect(second).toBe(first);
  });
});
