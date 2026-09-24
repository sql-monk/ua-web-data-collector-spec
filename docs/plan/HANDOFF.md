# Стан робіт і точка відновлення

Оновлено: 2026-09-24.

## 1. Як відновити роботу

1. Прочитати `docs/IMPLEMENTATION_PLAN.md` (конвеєр, ролі субагентів, хвилі), `docs/plan/ledger.md` (стан кожного WP), цей файл.
2. Перевірити середовище: `git -C C:\repos\webscraper worktree list`, `gh pr list`, `docker ps`.
3. Продовжити з розділу 4 «Наступний крок».

Рольові інструкції субагентів — `.claude/agents/*.md`; з 2026-09-24 вони доступні як `subagent_type` напряму (`wp-implementer`, `wp-tester`, `wp-code-reviewer`, `wp-spec-reviewer`, `wp-docs-writer`, `wp-security-reviewer`, `source-canary`).

## 2. Що зроблено (злито в `main`, усі з зеленим CI)

| PR | Пакет | Зміст |
|---|---|---|
| — (`7223bec`) | WP-00 PR1 | Python 3.13 + uv, CLI-контракт §16.2, блок мережі в тестах (pytest-socket), structlog + redaction, pre-commit + gitleaks, CI |
| #1 (`1f2fbc8`) | WP-01C | Shared contracts: UUIDv7, identity/idempotency, 5 enum §5.5, temporal + bitemporal, Money/Contact, artifacts/upload claim, projection command/receipt/ack, `encode_event` (canonical bytes, 256 KiB), current document, resolution decision, release manifest; 34 JSON Schema snapshots + drift-check у CI; ADR-0003/0004 |
| #2 (`643d41b`) | WP-00 PR2 | Multi-stage image `collector`, Compose profiles/мережі/secrets, one-shots `migrate-postgres`/`ensure-mongo`, health-стаб, SBOM + trivy; ADR-0002, runbooks |
| #3 (`758c68c`) | WP-01A PR1 | PostgreSQL: 13 таблиць §9.1, черга `FOR UPDATE SKIP LOCKED` + lease, глобальний origin limiter (R-53), worker pools/scale commands, audit append-only, 8 ролей §13, партиціонування; ADR-0005, `docs/persistence/postgres.md` |
| #4 (`715d54e`) | WP-00 PR3 | React 19 + Vite 7 GUI scaffold, non-root Nginx із CSP, profile `gui`, job `web` + e2e проти живого стека в CI; закриває WP-00 |
| #5 (`f87df17`) | WP-01D PR1 | Worker runtime: claim-loop, lease heartbeat, **self-fencing за часом**, drain, singleton scheduler, hot concurrency, дешевий liveness-probe; ADR-0006, `docs/workers.md`, runbook |
| #6 (`43ee69f`) | WP-01A PR2 | Artifacts, upload claims, projection tasks/acks, outboxes, entity index, LOGIN-ролі (`db roles --with-login`), `queue.release`, транзакційний audit; ADR-0007 |
| #13 (`b9bed91`) | WP-01D PR1c | Handler plumbing: defer/not-before/retry schedule, lazy registry + context, crawl/projection backends, transactional fenced ack, domain scheduler ticks; CR-1 cancellation race fixed; CI green 6/6 |

CI має 6 jobs: `python`, `web`, `integration (PostgreSQL 18)`, `docker` (build + SBOM + trivy обох образів + clean-host `up --wait` + e2e), `pre-commit`, `gitleaks`.

## 3. WP-01A PR2 — злито (PR #6, `43ee69f`)

- Branch `wp/01a-2-artifacts-projection`, worktree `.worktrees/wp-01a`, PR #6.
- Реалізацію з WIP `c5f6f70` доведено до кінця і пройдено всі gates: testing pass, code review r2 approve, security approve, spec review r2 accept, docs (ADR-0007, правка ТЗ §9.1). Звіти — `docs/plan/reports/WP-01A/*-pr2*.md`.
- Злиття — лише merge-commit (без squash/rebase): `.gitleaksignore` прив'язаний до SHA `bad6a25`.
- Лишається поза PR2: per-role DSN у runtime (`deps/WP-01A-to-WP-00.md` §4, `deps/WP-01A-to-WP-01D.md` §1) — блокер pilot; вимоги до parser — `deps/WP-01A-to-WP-02.md`.

## 4. Наступний крок

1. **WP-01D PR1c завершено** (PR #13, `b9bed91`): залежність WP-01A PR3a спожито, усі gates і CI 6/6 зелені. Це розблоковує runtime-частини WP-01B PR3, WP-02 PR2 і WP-04 PR2.
2. Наступна незалежна передумова хвилі 1 — **WP-00 PR5 object-store/component secrets**. Паралельно готові WP-01B PR1 (Mongo schema) і WP-01D PR2 (global permit client); картки WP-01B/WP-02/WP-04 вже створені й актуальні в `docs/plan/cards/`.
3. Потім хвиля 2: WP-03, WP-05, WP-07, WP-09.

## 5. Відкриті борги та ризики

| # | Що | Owner | Стан |
|---|---|---|---|
| 1 | ~~Runtime-процеси на superuser DSN~~ — закрито PR #7 + PR #8; залишок: export-worker під `collector_scheduler` (§13 exporter read-only) | WP-11A / WP-01A | accepted до першого export handler або pilot, тест-вартовий |
| 2 | `queue.release` для планового drain | WP-01D | закрито — runtime використовує `queue.release` (PR #8) |
| 3 | `command_timeout` в engine — зміна у файлі WP-01A, потребує підтвердження owner | WP-01A PR2 | підтверджено |
| 4 | Role-wide drain barrier (зараз per-instance), origin limiter runtime, Compose/Swarm adapters | WP-01D PR2/PR3 | картка WP-01D |
| 5 | TOCTOU у scheduler-тіку (нешкідливо для ідемпотентного maintenance) | WP-01D | «Відомі ризики» картки |
| 6 | 2 unfixed HIGH CVE (perl, zlib) у базовому образі; 1 HIGH у GUI-образі | WP-13 | датований risk acceptance в ADR-0002, тригер перегляду — merge WP-02 |
| 7 | Щотижневий scan за розкладом (§13 вимагає «щотижня і на кожен PR») | WP-13 | знахідка F-2 пострев'ю PR2 |
| 8 | `api` у мережі `ingress` має необмежений egress замість «лише OIDC» | WP-11A | accepted, коментар у compose |
| 9 | Health-endpoint розкриває версії/внутрішні адреси без auth | WP-11A | accepted |
| 10 | Immutable registry digest для app-образів | WP-14 | accepted |

## 6. Уроки конвеєра (варто зберегти в наступних картках)

- Локальний прогін на Windows приховував три різні класи дефектів, які спіймав лише CI або окремий рев'юер: блок мережі (`ProactorEventLoop` минав `socket.connect`), тест, що потребував реального loopback-з'єднання (на Linux `pytest-socket` блокує повністю), бюджет healthcheck (на 2-ядерному runner-і проба не вкладалась).
- Тест-орієнтовані виправлення двічі закривали лише той сценарій, який відтворював тест: self-fencing спрацьовував на виняток від БД, але не на зависання; e2e-гейт по універсальній `CI` ламав би job без стека.
- Найдорожча знахідка — 23 тести, що мовчки пропускалися в CI. У картках наступних WP варто одразу вимагати «тест проти мовчазного skip».
- Dependency-процедура (запит у `docs/plan/deps/`, рішення оркестратора, фіксація `resolved`) відпрацювала — паралельні агенти жодного разу не редагували чужі owned files без погодження.
