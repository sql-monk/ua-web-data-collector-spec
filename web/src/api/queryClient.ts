import { QueryClient } from '@tanstack/react-query';

import { API_SCHEMA_VERSION } from './schemaVersion';

/**
 * Cache keys містять версію API/schema (§7.7): після зміни контракту старі записи кешу не
 * матчаться і не показуються як свіжі. Хелпер використовують усі query hooks WP-11C.
 */
export function apiQueryKey(...parts: readonly (string | number)[]): readonly unknown[] {
  return ['api', API_SCHEMA_VERSION, ...parts];
}

/**
 * Кеш живе ЛИШЕ в пам'яті вкладки: жодного persister-а у `localStorage`/`sessionStorage`
 * чи IndexedDB (§7.7 «контакти не кешуються в browser storage», §13). Перезавантаження
 * сторінки має заново питати BFF, а не відновлювати персональні дані з диска.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
    mutations: {
      // Mutation не вважається виконаною до server acknowledgement (§7.7) — без retry,
      // щоб не повторювати неідемпотентну дію без свідомого idempotency key.
      retry: 0,
    },
  },
});
