# ADR-0005: PostgreSQL job queue + outbox замість брокера повідомлень

| Поле | Значення |
|---|---|
| Date | 2026-09-22 |
| Owner | WP-01A |
| Status | accepted |

## Context

§7.2 ТЗ прямо приписує MVP-реалізацію черги: «Використати PostgreSQL job table з `FOR UPDATE
SKIP LOCKED`, lease timeout, `attempt`, `not_before`, унікальним idempotency key і dead-letter
status. Це скорочує кількість сервісів і гарантує транзакційний outbox.» Той самий параграф
задає вимірювані критерії, коли це рішення переглядається: «Перехід на RabbitMQ/Redpanda
допускається лише після виміряної межі: понад 100 jobs/s стабільно, черга понад 1 млн pending
jobs або потреба в незалежному масштабуванні багатьох типів споживачів. Перехід оформлюється
ADR і не змінює job payload contract.»

§7.6/FR-033/R-53 (REVIEW.md) додають другу вимогу до того самого сховища: горизонтальне
масштабування worker pools (discovery/fetch/browser) не повинно множити request rate до
джерела — потрібен **canonical** глобальний origin rate limiter, а не per-container семафор,
інакше N реплік одного role помножують ефективний rate у N разів попри одну й ту саму policy
джерела (R-53 — критична регресія попереднього рев'ю ТЗ).

Цей ADR фіксує рішення, ухвалене §7.2/§7.6 буквально (не відхилення від ТЗ), формалізує
вимірювані критерії переходу і документує, що саме не зміниться, якщо перехід відбудеться —
щоб майбутній ADR про заміну черги мав чітку контрольну точку, а не переписував job payload
contract заодно з транспортом.

## Decision

### Job queue — PostgreSQL таблиця, не брокер

`crawl_jobs` (§9.1, реалізація `src/collector/persistence/postgres/repositories/queue.py`) —
єдина черга MVP:

- **claim** — `SELECT … FOR UPDATE SKIP LOCKED` за `status IN ('pending','retry') AND
  not_before <= now`, `ORDER BY priority DESC, not_before, job_id`; конкурентні claimers не
  блокуються один на одного (SKIP LOCKED) і не отримують той самий job двічі (row lock);
- **lease** — `lease_owner`/`lease_expires_at` на рядку job, продовжується `heartbeat`;
  прострочений lease повертає job у `pending` (`recover_expired_leases`), `attempt` зберігається;
- **idempotency key** — unique constraint на `crawl_jobs.idempotency_key`; повторний `enqueue`
  з тим самим ключем повертає той самий job, без дубля (`INSERT … ON CONFLICT DO NOTHING` +
  `SELECT`, вимагає READ COMMITTED — `docs/persistence/postgres.md` розділ 4);
- **dead letter** — після `max_attempts` job переходить у `quarantined`, і в тій самій
  транзакції з'являється запис `dead_letters`;
- **транзакційний outbox** — та сама PostgreSQL-транзакція, що змінює статус job (`complete`,
  `retry`), може атомарно записати результат роботи (у PR1 — audit/статус control plane; у PR2
  — `outbox_events` разом з `parse_attempts`/`projection_tasks`). Це і є перевага §7.2
  «гарантує транзакційний outbox»: job-статус і результат ніколи не розходяться, бо це один
  COMMIT, а не queue-ack плюс окремий APPEND у брокер.

### Чому не RabbitMQ/Redpanda зараз

- **Менше сервісів.** MVP на одному хості (§7.5, Q-013 default) уже має PostgreSQL, MongoDB,
  S3/MinIO; окремий broker — четвертий stateful сервіс з власним failure mode, моніторингом і
  backup-стратегією, без вимірюваної потреби на цьому обсязі.
- **Транзакційний outbox без dual-write.** Брокер живе поза транзакцією БД: запис у чергу і
  запис результату в PostgreSQL — два окремих commit, отже потрібен окремий outbox-relay
  процес і delivery semantics (at-least-once з дедуплікацією на споживачі) там, де
  PostgreSQL дає це безкоштовно однією транзакцією.
- **Той самий canonical store для origin limiter.** R-53 вимагає, щоб глобальний permit-облік
  (rate token + concurrency slot на origin) не залежав від того, скільки реплік worker role
  запущено. `origin_rate_buckets`/`origin_rate_permits` (§7.6, `repositories/limiter.py`)
  використовують той самий PostgreSQL і той самий примітив (`SELECT … FOR UPDATE` на bucket),
  що й черга — одна модель узгодженості для обох механізмів, а не черга в одному сховищі й
  лімітер в іншому (Redis, локальний семафор).

### Вимірювані критерії переходу (§7.2, дослівно)

Перехід на RabbitMQ/Redpanda розглядається **лише** коли виконано хоча б один із критеріїв,
виміряний на production-подібному навантаженні (не синтетичний benchmark на порожній БД):

| Критерій | Поріг |
|---|---|
| Стабільна пропускна здатність черги | понад 100 jobs/s |
| Глибина черги | понад 1 млн pending jobs |
| Незалежне масштабування споживачів | потреба масштабувати багато типів споживачів (job types) незалежно один від одного за межами того, що дає `worker_pools`/`scale_commands` (§7.6) |

У WP-01A PR1 жоден із цих критеріїв не вимірювався — це явно поза acceptance PR1
(`docs/plan/reports/WP-01A/implementation-pr1.md`, розділ «Що не перевірено»): «продуктивність
під навантаженням (§15: 100 jobs/s, 1 млн pending) не вимірювалась — це критерії переходу на
брокер, не acceptance PR1». Перехід оформлюється **новим ADR** із вимірами, що підтверджують
поріг, а не превентивно.

### Що НЕ зміниться при переході на брокер (job payload contract)

§7.2 прямо каже: перехід «не змінює job payload contract». Це означає, що структура job-у,
якою користуються інші WP через типізовані репозиторії (`NewJob`: `job_type`,
`idempotency_key`, `args`, `priority`, `max_attempts`, `not_before`, `run_id`, `source_id`),
лишається стабільною незалежно від транспорту:

- `idempotency_key` як ідентичність job-а (не запит «постав ще раз») — семантика зберігається;
  брокер, як і PostgreSQL, має гарантувати «той самий ключ → та сама логічна робота»;
- `args` — bounded control extension (≤ 8 KiB, R-27 — жодного domain payload) — обмеження
  розміру повідомлення лишається тим самим незалежно від того, серіалізує його PostgreSQL
  JSONB чи брокер;
- `attempt`/`max_attempts`/dead-letter семантика — перехід на брокер підміняє лише механізм
  lease/visibility timeout, не контракт «скільки спроб і що відбувається після вичерпання»;
- типізовані помилки викликача (`errors.LeaseNotOwnedError`, `errors.ConflictError` тощо) —
  репозиторний API лишається тим самим фасадом; інші WP і далі не пишуть SQL/broker-протокол
  напряму (картка WP-01A, «Спільні вимоги»: «Жодних SQL-рядків у інших WP — лише через ці
  репозиторії»).

Що **зміниться** при переході — це виключно транспортний рівень усередині
`repositories/queue.py` (claim/lease реалізація) і, можливо, спосіб доставки
outbox-подій (PR2 `outbox_events`) споживачам поза PostgreSQL-транзакцією викликача; сам факт
переходу не є приводом міняти jobs schema чи публічний контракт `NewJob`/`errors.*`.

### Origin rate limiter — canonical у PostgreSQL, Redis не є source of truth (R-53)

`origin_rate_buckets`/`origin_rate_permits` — той самий вибір, що й для черги, і з тієї самої
причини: один рядок під `FOR UPDATE` на bucket серіалізує **всі** репліки discovery/fetch/
browser worker role незалежно від їхньої кількості, тож видача rate tokens і concurrency slots
детермінована і не залежить від того, скільки контейнерів запущено (перевірено
`tests/integration/postgres/test_limiter.py::
test_eight_parallel_acquirers_get_exactly_one_slot_with_concurrency_one` — 8 незалежних
транзакцій, рівно 1 виданий permit при `max_concurrency=1`).

Технологічна таблиця ТЗ (§8) явно допускає Redis-сумісний кеш «для translation memory hot
cache; high-throughput limiter optimization після ADR», але фіксує: «v1 global rate permit
canonical у PostgreSQL; Redis не є source of truth». Це узгоджується з рішенням цього ADR:
Redis (чи будь-який зовнішній кеш) може з'явитись як **оптимізація читання** (менше `SELECT …
FOR UPDATE` при дуже високому QPS), але не як друге джерело істини — інакше два незалежних
сховища permit-стану можуть розійтися під конкурентним записом, і R-53 (множення request rate
під масштабуванням) повернеться в іншій формі. Введення Redis-шляху для лімітера — окремий
майбутній ADR з power/latency вимірами, так само, як перехід черги на брокер.

## Consequences

- **Один primary store для двох критичних інваріантів** (job delivery і origin rate) спрощує
  операційну модель MVP: один backup/PITR план (§7.4), одна модель consistency, немає
  cross-store reconciliation між чергою і лімітером.
- **Масштабування черги обмежене вертикальним масштабуванням PostgreSQL** і кількістю
  paralellних `claim`-сесій, доки не досягнуто порогів §7.2 — це прийнятний компроміс для MVP
  на одному хості (Q-006/Q-013 default), але вимагає моніторингу `queue_oldest_age_seconds`,
  `crawl_jobs_total{status}` (§14.1), щоб поріг переходу було видно заздалегідь, а не
  постфактум після деградації.
- **`ix_crawl_jobs_claimable_order`** (`docs/persistence/postgres.md` розділ 6) — конкретний
  доказ того, що PostgreSQL-черга масштабується керованими засобами (індексація), а не лише
  «поки не стане проблемою»: вимір 331 мс → 0.18 мс на 400 000 pending jobs показує, що вузьке
  місце в MVP — це відсутній index, не сама архітектура «черга в PostgreSQL».
- **Майбутній перехід на брокер лишається дешевим рефакторингом транспорту**, а не переписом
  контракту: споживачі (WP-01D discovery/fetch/browser/parse/projector/translation workers)
  використовують лише `repositories.queue.*`/`NewJob`/`errors.*`, тож заміна реалізації під
  капотом не вимагає зміни коду worker-ів, лише самого репозиторію та інфраструктури.
- **Redis (чи інший кеш) для лімітера — можлива майбутня оптимізація, не заміна.** Якщо
  latency `acquire_permit` під навантаженням стане проблемою до появи `1 млн pending`/`100
  jobs/s`, рішення — локальне кешування дозволів з коротким TTL перед PostgreSQL-запитом, а не
  перенесення джерела істини; будь-яка зміна тут теж вимагає окремого ADR (§8 ТЗ).

## Related

- ТЗ: §7.2 (черга MVP, критерії переходу), §7.6 (worker pools, глобальний limiter), §8
  (технологічна таблиця — Redis-рядок), §9.1 (`crawl_jobs`, `origin_rate_buckets`/
  `origin_rate_permits`), §15 (продуктивність — 100 jobs/s / 1 млн pending), §20 (формат ADR).
- REVIEW.md: R-53 (критична регресія — глобальний лімітер canonical у PostgreSQL).
- Реалізація: `src/collector/persistence/postgres/repositories/{queue.py,limiter.py}`,
  `src/collector/persistence/postgres/models/{queue.py,limiter.py}`,
  `migrations/postgres/versions/20260922_0002_claim_index.py`.
- Документація: `docs/persistence/postgres.md` (розділи 3, 4, 6), `docs/runbooks/migrations.md`.
- Звіти: `docs/plan/reports/WP-01A/implementation-pr1.md` (розділ «Виправлення після gate 2» —
  вимір `ix_crawl_jobs_claimable_order`; розділ «Що не перевірено» — §15 поза scope PR1),
  `docs/plan/reports/WP-01A/spec-review-pr1.md` (R-53 evidenced, розділ 4).
