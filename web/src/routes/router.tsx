import type { RouteObject } from 'react-router';

import { AppShell } from '~/components/AppShell';
import { RouteFallback } from '~/components/RouteFallback';

/**
 * Route-level code splitting (§7.7, §8): кожен маршрут підвантажується власним chunk-ом через
 * `lazy` React Router. У WP-00 маршрутів два (placeholder і 404); екрани §7.7 додає WP-11C,
 * додаючи сюди записи з таким самим `lazy` — bundle початкового завантаження не росте.
 *
 * Тут — лише таблиця маршрутів. `createBrowserRouter` викликає `src/main.tsx`: створення
 * data router-а запускає навігацію вже на імпорті модуля, а це не має відбуватися у тестах.
 */
export const routes: RouteObject[] = [
  {
    path: '/',
    Component: AppShell,
    HydrateFallback: RouteFallback,
    children: [
      {
        index: true,
        lazy: async () => {
          const { OverviewPage } = await import('~/routes/OverviewPage');
          return { Component: OverviewPage };
        },
      },
      {
        path: '*',
        lazy: async () => {
          const { NotFoundPage } = await import('~/routes/NotFoundPage');
          return { Component: NotFoundPage };
        },
      },
    ],
  },
];
