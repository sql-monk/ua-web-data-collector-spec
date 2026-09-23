import { PLANNED_SCREENS } from '~/routes/screens';

/**
 * Єдина сторінка каркаса WP-00 PR3 (FR-034 — лише каркас). Дев'ять екранів §7.7 реалізує
 * WP-11C; тут вони перелічені як план, щоб маршрутизація і мова інтерфейсу були перевірювані
 * вже зараз.
 */
export function OverviewPage() {
  return (
    <section aria-labelledby="overview-title">
      <h1 id="overview-title">UA Web Data Collector — operator GUI</h1>
      <p>
        Це каркас інтерфейсу оператора. Мова інтерфейсу за замовчуванням — українська. Дані ще не
        завантажуються: API-клієнт генерується з OpenAPI, а екрани наповнює WP-11C.
      </p>

      <h2>Заплановані екрани (§7.7 ТЗ)</h2>
      <ol>
        {PLANNED_SCREENS.map((screen) => (
          <li key={screen.id}>
            <strong>{screen.title}</strong> — {screen.summary}
          </li>
        ))}
      </ol>

      <p className="note">
        Стан WP-00: маршрути, збірка, перевірки та контейнер GUI. Жодних токенів у браузерному
        сховищі — сесія живе у <code>HttpOnly</code>-cookie від BFF.
      </p>
    </section>
  );
}
