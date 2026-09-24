# WP-01A PR3a — security review

Перевірено релевантну частину threat model §13: SQL injection у нових запитах, least privilege
для component LOGIN-ролей, audit переходу circuit breaker, видалення outbox, secret leakage,
URL hash collision та fail-closed поведінку internal events.

## Вердикт

**approve**. Відкритих critical/high/medium security findings немає.

## Контролі

- Усі runtime значення в repository queries передаються як SQLAlchemy bind parameters;
  динамічного SQL із URL/actor/reason немає.
- MD5 використано лише як компактний ключ індексу validators; повний `requested_url`
  порівнюється в тому самому запиті, тому collision не повертає validators іншого URL.
- `projection.command` лишається fail-closed internal topic і за замовчуванням не видається
  publisher-у; видалення дозволене лише після terminal task + acknowledgement.
- `record_route_failure` вимагає непорожні actor/reason; state transition і audit commit-яться
  в одній транзакції. Некоректні threshold/duration відхиляються до запису.
- Попередній table-level UPDATE `source_routes` у fetcher-а явно відкликається перед
  column-level GRANT, тому повторний `db roles` справді звужує доступ.
- Projector для reconciliation має SELECT, але не отримує UPDATE публікаційних полів outbox;
  чужі component roles перевірені негативними LOGIN-тестами.
- Scheduler отримує DELETE лише на `outbox_events`, не на projection/change/source tables;
  bounded purge сам додатково фільтрує topic, age та acknowledgement.
- Нових secrets, credentials, приватних fixtures або логування headers/payload у diff немає;
  pre-commit secret scan пройшов.

## Залишкові ризики

| Ризик | Оцінка | Owner / дія |
|---|---|---|
| Компрометований fetcher із прямим SQL може змінити дозволені circuit-state колонки без repository audit | low; DB role — внутрішній trust boundary, але blast radius обмежений одним control row class | WP-13: переглянути SECURITY DEFINER command API, якщо worker credentials вважатимуться hostile boundary |
| `collector_scheduler` може напряму DELETE outbox поза `purge_published` | low; maintenance і publisher наразі свідомо поділяють одну роль | WP-12/WP-13: за потреби виділити maintenance role або DB function після появи runtime maintenance |
| Generated MD5 індекс не є криптографічним доказом URL identity | accepted by design | Повне порівняння URL обов'язкове; regression test і docstring не дозволяють прибрати його |

Ці ризики не внесені PR3a як нова вразливість і не блокують merge; вони явно передані
власникам фінального lifecycle/security review.
