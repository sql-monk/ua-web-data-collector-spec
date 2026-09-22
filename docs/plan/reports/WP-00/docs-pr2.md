# WP-00 PR2 — звіт документування (`wp/00-2-docker-compose`)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-00 / PR2 «Docker images + Compose profiles» |
| Branch / worktree | `wp/00-2-docker-compose` / `.worktrees/wp-00-2`, HEAD `eb280a6` |
| Етап | 5 (docs), після вердикту `accept` пострев'ю (`docs/plan/reports/WP-00/spec-review-pr2.md`) |
| Джерела | картка `docs/plan/cards/WP-00.md` (розділ PR2 і «Docs»); `implementation-pr2.md`, `testing-pr2.md`, `code-review-pr2.md`, `security-pr2.md`, `spec-review-pr2.md`; ТЗ §7.5, §13, §16.2, §20; `docker-compose.yml`, `Dockerfile`, `deploy/compose/**` |

## Що зроблено

1. **`docs/decisions/0002-docker-compose-single-host.md`** (уже написаний реалізатором —
   перевірено й доповнено):
   - Виправлено знахідку **F-1** пострев'ю (`spec-review-pr2.md`): ADR цитував healthcheck
     mongo як `--host "$(hostname -i)"`, хоча після gate 3 (CR-1) `docker-compose.yml:218`
     використовує `hostname -I | tr ' ' '\n' | grep -m1 -E '^[0-9]+(\.[0-9]+){3}$'` з явним
     вибором першої IPv4 (`hostname -i` на dual-stack віддає кілька адрес, IPv6 першою, що
     ламає `--host`). Текст секції «MongoDB — single-member replica set» приведено до
     фактичного коду.
   - Додано розділ «Прийняті знахідки пострев'ю / spec-review (дата 2026-09-22)» з двома
     датованими risk acceptance, перевіреними в коді:
     - **F-2** (§13, low) — dependency/image scanning вимагається «щотижня і на кожен PR»,
       а `.github/workflows/ci.yml` має лише per-PR крок; owner WP-13, дедлайн 2026-12-22;
     - **F-3** (§7.5, low) — application image `collector` має лише mutable tag, без
       публікації immutable digest у registry; owner WP-14 разом із PR3.
   - Профілі, `--profile` vs `COMPOSE_PROFILES`, невалідність `--profile workers` без `core`,
     обмеження фіксованого `name: collector`, і всі три категорії датованих risk acceptance
     (MinIO uid 0 + `cap_drop`/`read_only`, секрети 0644, 2 unfixed HIGH CVE з ID/тригером)
     уже були описані реалізатором з датами й owner — звірено проти коду
     (`docker-compose.yml`, `deploy/compose/secrets/init-secrets.sh`), розбіжностей, крім
     F-1, не знайдено; дублювання не додавалося.
   - Розділ «Related» доповнено посиланнями на новий `docs/runbooks/rollback-image.md` і
     `spec-review-pr2.md`.
2. **`docs/runbooks/clean-host-start.md`** і **`deploy/compose/README.md`** (написані
   реалізатором) — перевірено проти `docker-compose.yml` і звітів: команди, таблиця profiles,
   мережі, секрети (0644, генерація `init-secrets.sh`), обмеження `name: collector` для
   паралельних checkout-ів, `--profile` заміняє `COMPOSE_PROFILES` — усе відповідає коду й
   уже покриває вимогу картки. Змін по суті не вносилося (лише файли, на які вони
   посилаються, оновлено нижче).
3. **`docs/runbooks/rollback-image.md`** (новий файл) — як відкотити app image `collector`
   за tag/digest (`COLLECTOR_IMAGE`), перевірка OCI-labels (`docker inspect --format
   '{{index .Config.Labels "org.opencontainers.image.revision"}}'` — синтаксис перевірено
   локально на закешованому `collector:dev`, а формат виводу підтверджено
   `testing-pr2.md` §1.2), відкат `docker-compose.yml` без втрати named volumes
   (`down` без `-v`, `git checkout <sha> -- docker-compose.yml deploy/compose`). Зафіксовано
   як відоме обмеження (F-3): відсутність publish digest у registry — rollback за digest
   `operationally unverified` до появи registry pipeline (owner WP-14); локальний
   image cache / детермінований ребілд із `uv.lock` — робочі варіанти вже сьогодні.
   Картку PR3 оновлено: пункт «`docs/runbooks/rollback-image.md` (заготовка)» перенесено
   з PR3 у Docs PR2 як виконаний, замість заготовки.
4. **`README.md`** кореня — додано розділ «Запуск стека (Docker Compose, PR2)»:
   `init-secrets.sh`, clean-host команда §16.2 (`docker compose --profile core --profile
   workers up -d --wait`), посилання на `docs/runbooks/clean-host-start.md`,
   `docs/runbooks/rollback-image.md`, `deploy/compose/README.md`,
   `docs/decisions/0002-docker-compose-single-host.md`. Без дублювання деталей quickstart
   PR1 (uv/pre-commit лишились у «Швидкий старт розробника»). Оновлено вступний абзац
   («Стан на PR2» замість «Стан на PR1») і вимогу Docker (версія Engine/Compose, коли саме
   потрібен). Заголовок «План і стан робіт» піднято до `##`, щоб не опинитися вкладеним
   під новий розділ.
5. **`docs/plan/cards/WP-00.md`** — розширено «Owned files» PR2 фактично зміненими файлами
   (знахідка **F-4** пострев'ю): `tests/**`, `pyproject.toml`, `uv.lock`, `.gitignore`,
   `.gitattributes`, `docs/runbooks/**`, з коментарем, що це фіксація фактичного scope за
   рішенням пострев'ю (`spec-review-pr2.md`, F-4), а не розширення обсягу; forbidden-файли
   (`docs/research/**`, `TECHNICAL_SPECIFICATION.md`, `REVIEW.md`) не змінювались —
   перевірено пострев'ю. Рядок Docs PR2/PR3 синхронізовано з фактом (rollback-image.md — у
   PR2).
6. **`docs/plan/reports/WP-00/docs-pr2.md`** — цей звіт.

Docstrings коду не змінювалися (код не чіпався); публічні інтерфейси PR2
(`src/collector/api/health.py`, `src/collector/cli.py`) вже мають docstrings за оцінкою
`code-review-pr2.md` (approve).

## Lint

```text
$ npx --yes markdownlint-cli2 README.md docs/decisions/0002-docker-compose-single-host.md \
  docs/runbooks/rollback-image.md docs/plan/cards/WP-00.md \
  docs/runbooks/clean-host-start.md deploy/compose/README.md
markdownlint-cli2 v0.23.3 (markdownlint v0.41.1)
Linting: 6 files
Summary: 0 issues in 0 files
[exit 0]

$ npx --yes markdown-link-check -c .markdown-link-check.json README.md
15 links checked, усі [✓] (включно з новими docs/runbooks/rollback-image.md,
docs/decisions/0002-docker-compose-single-host.md)
[exit 0]

$ npx --yes markdown-link-check -c .markdown-link-check.json docs/runbooks/clean-host-start.md
No hyperlinks found! 0 links checked.
[exit 0]
```

`docs/runbooks/rollback-image.md`, `docs/decisions/0002-docker-compose-single-host.md` і
`docs/plan/cards/WP-00.md` не містять markdown-гіперпосилань (перевірено `grep`), тому
`markdown-link-check` для них не запускався окремо — покриті проходом README.md як цілі
посилань.

## Файли

- `docs/decisions/0002-docker-compose-single-host.md` (оновлено: F-1 fix + розділ
  «Прийняті знахідки пострев'ю»)
- `docs/runbooks/rollback-image.md` (новий)
- `README.md` (оновлено: розділ «Запуск стека»)
- `docs/plan/cards/WP-00.md` (оновлено: Owned files PR2, Docs PR2/PR3)
- `docs/plan/reports/WP-00/docs-pr2.md` (новий, цей звіт)

Без змін по суті (перевірено, розбіжностей з кодом не знайдено, окрім F-1):
`docs/runbooks/clean-host-start.md`, `deploy/compose/README.md`.
