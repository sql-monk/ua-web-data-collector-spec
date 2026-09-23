/** Плейсхолдер, поки підвантажується chunk маршруту (route-level code splitting, §7.7). */
export function RouteFallback() {
  return (
    <p role="status" aria-live="polite">
      Завантаження…
    </p>
  );
}
