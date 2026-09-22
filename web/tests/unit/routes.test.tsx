import type { ReactElement } from 'react';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it } from 'vitest';

import { AppShell } from '~/components/AppShell';
import { NotFoundPage } from '~/routes/NotFoundPage';
import { OverviewPage } from '~/routes/OverviewPage';
import { routes } from '~/routes/router';
import { PLANNED_SCREENS } from '~/routes/screens';

/**
 * Дерево маршрутів монтується через декларативний `MemoryRouter`, а не `createMemoryRouter`:
 * data router створює `Request` з `AbortSignal` jsdom, який undici Node відхиляє
 * («Expected signal to be an instance of AbortSignal»). Реальну навігацію data router-а у
 * браузері перевіряє tests/e2e/smoke.spec.ts; тут — вміст екранів і межі lazy-chunk-ів.
 */
function renderWithShell(initialPath: string, page: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="*" element={page} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('каркас operator GUI', () => {
  it('placeholder-сторінка українською з заголовком §7.7 (FR-034)', () => {
    renderWithShell('/', <OverviewPage />);

    expect(
      screen.getByRole('heading', { level: 1, name: 'UA Web Data Collector — operator GUI' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('contentinfo')).toHaveTextContent('Інтерфейс українською');
  });

  it('перелічує дев’ять екранів §7.7 як план WP-11C', () => {
    renderWithShell('/', <OverviewPage />);

    expect(PLANNED_SCREENS).toHaveLength(9);
    expect(screen.getAllByRole('listitem')).toHaveLength(9);
    for (const planned of PLANNED_SCREENS) {
      expect(screen.getByText(planned.title)).toBeInTheDocument();
    }
  });

  it('невідомий маршрут дає українську сторінку 404 з поверненням на головну', () => {
    renderWithShell('/немає-такого', <NotFoundPage />);

    expect(
      screen.getByRole('heading', { level: 1, name: 'Сторінку не знайдено' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'На головну' })).toHaveAttribute('href', '/');
  });
});

describe('route-level code splitting (§7.7, §8)', () => {
  const children = routes[0]?.children ?? [];

  it('кожен листовий маршрут оголошено через lazy, а не статичний Component', () => {
    expect(children.length).toBeGreaterThan(0);
    for (const route of children) {
      expect(typeof route.lazy).toBe('function');
      expect(route.Component).toBeUndefined();
    }
  });

  it('lazy-межі резолвляться у реальні компоненти сторінок', async () => {
    const loaded = await Promise.all(
      children.map(async (route) => {
        const module = await (route.lazy as () => Promise<{ Component: () => ReactElement }>)();
        return module.Component;
      }),
    );

    expect(loaded).toContain(OverviewPage);
    expect(loaded).toContain(NotFoundPage);
  });
});
