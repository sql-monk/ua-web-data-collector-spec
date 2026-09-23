import { Suspense } from 'react';
import { Outlet } from 'react-router';

import { RouteFallback } from '~/components/RouteFallback';

/**
 * Каркас застосунку: постійні header/footer і слот маршруту. Навігацію по дев'яти екранах
 * §7.7 додає WP-11C разом з RBAC-gating (видимість у UI — лише UX, авторизація — на сервері).
 */
export function AppShell() {
  return (
    <div className="app-shell">
      <header className="app-shell__header">
        <span className="app-shell__brand">UA Web Data Collector</span>
        <span className="app-shell__env">operator GUI · каркас WP-00</span>
      </header>

      <main className="app-shell__main">
        <Suspense fallback={<RouteFallback />}>
          <Outlet />
        </Suspense>
      </main>

      <footer className="app-shell__footer">
        Інтерфейс українською. Джерело істини — versioned API (FR-034).
      </footer>
    </div>
  );
}
