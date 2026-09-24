# ADR-0010: MongoDB domain projection і single-member replica set для локального MVP

| Поле | Значення |
|---|---|
| Date | 2026-09-24 |
| Owner | WP-01B |
| Status | accepted |

## Context

Каталожні й автомобільні документи мають різнорідні bounded attributes та читаються як цілі
картки. PostgreSQL водночас уже є canonical control plane для scheduler, tasks, lineage,
outbox і acknowledgements. Синхронний dual-write parser-а в дві БД заборонений (§7.3,
FR-020—FR-023).

MongoDB-транзакції потребують replica set. Q-010 задає safe default: single-member replica set
у локальному MVP і три data-bearing members у різних failure domains до HA production.

## Decision

- MongoDB є authoritative serving projection для catalog/vehicle current documents,
  append-only observations, versions і applied receipts; PostgreSQL не дублює domain payload.
- MongoDB лишається відновлюваною materialized projection з PostgreSQL tasks/artifact pointers
  та immutable S3/MinIO artifacts. Scheduler state у MongoDB заборонений.
- Локальний Docker Compose використовує single-member `rs0`. Runtime reads — primary з
  majority concern, writes — majority, projection-транзакції — snapshot/majority.
- Schema змінюється лише forward-only checksum migrations. Contract snapshots перетворюються
  на frozen `$jsonSchema` assets; validators проходять `warn → validate → error`.
- Індекси керуються декларативним маніфестом; зайві індекси лише звітуються, конфлікти не
  перебудовуються автоматично.
- Доступ розділений за компонентами: projector write без delete/DDL, compactor може видаляти
  лише version records, API/export read-only, scheduler/fetcher/parser без Mongo credentials.
- Single-member topology не допускається як HA production topology. До такого запуску потрібні
  щонайменше три data-bearing members у різних failure domains, перевірені backups/restores,
  моніторинг replication lag і документовані RPO/RTO.

## Consequences

- Catalog/vehicle read path не потребує unbounded runtime join з PostgreSQL і може зберігати
  bounded documents, які читаються разом.
- Parser пише лише PostgreSQL task/artifact pointer; projector і receipts забезпечують
  at-least-once delivery без синхронної cross-database транзакції.
- Mongo schema drift виявляється до застосування наступної міграції, а rollback виконується
  лише новою forward-міграцією.
- Local/dev має production-подібні transaction semantics, але втрата єдиного Mongo-вузла дає
  downtime до restore/rebuild. Це прийнятий ризик MVP, не гарантія доступності.
- Повні crash/replay/reconcile/restore semantics належать WP-01B PR2—PR3; compaction і archive
  locator — PR4.

## Related

- `TECHNICAL_SPECIFICATION.md`: §7.3—§7.4, §8, §9.2, §13, Q-010.
- `REVIEW.md`: R-24, R-25, R-34, R-36, R-37, R-42.
- `docs/persistence/mongo.md`.
- `docs/plan/cards/WP-01B.md`.
