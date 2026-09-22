import { Link } from 'react-router';

export function NotFoundPage() {
  return (
    <section aria-labelledby="not-found-title">
      <h1 id="not-found-title">Сторінку не знайдено</h1>
      <p>Такого маршруту немає. Перевірте адресу або поверніться на головну.</p>
      <p>
        <Link to="/">На головну</Link>
      </p>
    </section>
  );
}
