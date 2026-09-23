import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { routes } from '~/routes/router';

/**
 * Контракт зібраного артефакту (§7.7 «route-level code splitting», §8, §13).
 *
 * `tests/unit/routes.test.tsx` перевіряє намір — що маршрути оголошені через `lazy`.
 * Цей файл перевіряє результат: що Rollup справді розрізав bundle і що в `dist/` не
 * з'явилось того, чого там бути не має (inline-скрипт, який зламав би CSP `script-src 'self'`).
 *
 * `dist/` існує лише після `npm run build`, тому набір `describe.skipIf`: у ланцюжку §16.2
 * (`lint → test → build`) тести йдуть ДО збірки, а локально/після збірки вони виконуються.
 * Тому це доповнення до перевірки намірів, а не заміна їй.
 *
 * Код-рев'ю PR3 (H-1): цей skip був тихою втратою покриття — `npm run test` іде до
 * `npm run build`, і всі тести файлу зникали. Тепер їх виконує окремий крок
 * `npm run test:build` ПІСЛЯ збірки, і саме там skip заборонений: без `dist/` файл падає
 * гучно, а не пропускається.
 *
 * Пострев'ю (S-1): ознака «збірка обіцяна» — це режим Vite (`vitest run --mode
 * build-contract` у скрипті `test:build`), а НЕ змінна `CI`. GitHub Actions виставляє
 * `CI=true` в усіх job-ах, тому прив'язка до неї валила б звичайний `npm run test`, який
 * за контрактом §16.2 іде ще до `build`. Режим — крос-платформний і не залежить від
 * оточення взагалі.
 */
const BUILD_REQUIRED = import.meta.env.MODE === 'build-contract';
const DIST = join(process.cwd(), 'dist');
const ASSETS = join(DIST, 'assets');
const built = existsSync(ASSETS);

describe.skipIf(!built && !BUILD_REQUIRED)('зібраний артефакт: code splitting (§7.7)', () => {
  const assets = built ? readdirSync(ASSETS) : [];
  const jsChunks = assets.filter((name) => name.endsWith('.js'));

  it('кожен маршрут має власний chunk, а не один спільний bundle', () => {
    const routeChunks = jsChunks.filter((name) => /Page-[A-Za-z0-9_-]+\.js$/.test(name));

    expect(jsChunks.length).toBeGreaterThan(1);
    // Кількість lazy-маршрутів = кількість окремих chunk-ів сторінок.
    expect(routeChunks).toHaveLength(routes[0]?.children?.length ?? 0);
  });

  it('усі імена містять content hash — інакше кеш `expires 1y` у nginx отруїться', () => {
    for (const name of assets.filter((n) => !n.endsWith('.map'))) {
      expect(name, name).toMatch(/-[A-Za-z0-9_-]{8,}\.(js|css)$/);
    }
  });

  it('index.html не містить inline-скрипта — CSP script-src self без hash/nonce', () => {
    const html = readFileSync(join(DIST, 'index.html'), 'utf8');

    // Дозволені лише зовнішні модулі з src=; будь-який <script> з тілом ламає CSP §13.
    const inline = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)].filter(
      ([, attrs, body]) => !/\bsrc=/.test(attrs ?? '') || (body ?? '').trim() !== '',
    );
    expect(inline).toEqual([]);
    expect(html).not.toMatch(/<style\b[^>]*>[\s\S]*?\S[\s\S]*?<\/style>/);
    expect(html).not.toMatch(/\son[a-z]+=/i);
  });

  it('index.html тягне лише same-origin ассети — жодного CDN (CSP script-src/style-src self)', () => {
    const html = readFileSync(join(DIST, 'index.html'), 'utf8');

    const refs = [...html.matchAll(/\b(?:src|href)="([^"]+)"/g)].map((m) => m[1] ?? '');
    expect(refs.length).toBeGreaterThan(0);
    for (const ref of refs) {
      // `/assets/...` — той самий origin, який CSP дозволяє; `//cdn`, `https://` — ні.
      expect(ref, ref).toMatch(/^\/(?!\/)/);
    }
  });
});
