# Картка WP-01C — Shared data contracts

| Поле | Значення |
|---|---|
| Owner | wp-implementer (єдиний owner shared contracts до кінця проєкту — наступні зміни лише через dependency-запити) |
| Branch | `wp/01c-contracts` (від `wp/00-1-python-ci` після merge PR1 у `main` — rebase на `main`) |
| Worktree | `.worktrees/wp-01c` |
| Залежить від | WP-00 PR1 `merged` |
| Розблоковує | WP-01A, WP-01B, WP-01D, WP-02, WP-04 |
| Розмір | M (один PR; якщо > 800 рядків продуктивного коду — PR1 identity/temporal/enums, PR2 artifacts/projection/events, PR3 resolution/release) |
| Розділи ТЗ | §5.1, §5.4, §5.5, §7.3 (shapes `projection.command`, `domain.changed`, receipt), §9.2 (мінімальний контракт current document), §9.3, §9.4, §9.6, §9.8, §9.9, §10 п.8–10 |
| Рівні тестів §16.1 | 2 Contract (schema snapshots + compatibility), 9 Temporal, 1 Unit (identity hash, money, E.164/e-mail normalization) |
| Регресії REVIEW.md | R-18 (чотири осі стану), R-20 (nullable body), R-30 (command ≠ event), R-36 (mandatory projection version), R-37/R-42 (canonical event bytes + SHA-256), R-43/R-49 (temporal axes, bitemporal intervals), R-45 (resolution decision), R-46 (release manifest) |
| Q-питання | — |

## Scope

Єдине джерело версіонованих Pydantic v2 контрактів і їхніх JSON Schema snapshots у репозиторії, якими користуються всі інші WP. Контракти — чисті моделі без I/O; персистенція (SQLAlchemy/PyMongo mapping) — у WP-01A/01B.

## Out of scope

Таблиці/міграції PostgreSQL, Mongo validators (генеруються з цих схем у WP-01B, але живуть там), доменні поля vehicle/catalog (WP-07/WP-09 через dependency-запит до WP-01C), news article version tables (WP-01A), API DTO (WP-11A).

## Owned files

`src/collector/contracts/**`, `schemas/{events,mongo,releases}/**` (згенеровані JSON Schema snapshots + `README.md` як генерувати), `tests/contract/contracts/**`, `tests/unit/contracts/**`, `tests/fixtures/contracts/**`, `docs/contracts.md`, `docs/plan/reports/WP-01C/**`, `pyproject.toml` (лише додавання залежностей, якщо потрібні: `uuid-utils`/`uuid7`, `phonenumbers`, `idna`).

Forbidden: усе інше, зокрема `migrations/**`, `src/collector/persistence/**`.

## Вимоги

### 1. Ідентичність (§5.1, §9.3)

- `EntityId` = UUIDv7 (тип + генератор; тест монотонності в межах процесу).
- `SourceIdentity(source_id, source_item_id)`; `source_id` валідується проти `docs/research/source-registry.yaml` (loader read-only, кешований; тест, що всі 70 ID валідні і жодного дубліката).
- `identity_hash` — versioned алгоритм (`identity_hash_v1`): canonical URL + відсортовані стабільні атрибути → SHA-256; документ алгоритму в `docs/contracts.md`; golden fixtures.
- Fetch idempotency key: `source_id + normalized_url + planned_at_bucket + request_variant` — функція з golden тестом; `normalized_url` тут лише як вхід (нормалізація URL — WP-02, але контракт фіксує, що tracking-параметри видалені, а вихідний URL збережений окремо).
- Translation idempotency key: `article_version_id + target_language + provider + model_version + glossary_version`.
- Raw object key: `sha256(body)`.

### 2. Чотири осі стану + fetch_outcome (§5.5) — R-18

Закриті `StrEnum`: `SourceState`, `RouteState`, `EntityLifecycle`, `ContentAccess`, `FetchOutcome` — з точними значеннями §5.5. Функція `map_research_access_state()` для research-позначень (`free→full`, `body_unavailable→metadata_only`, `retryable→FetchOutcome.retryable`, решта однойменні) з тестом. Заборонено додавати інші enum «стану» в інших WP — зафіксувати в `docs/contracts.md`.

### 3. Часова модель (§5.1, §9.6) — R-43, R-49

- `SourceTime`: `source_event_at: datetime|None`, `source_updated_at: datetime|None`, `source_timezone_raw: str|None`, `source_time_precision: second|minute|hour|day|month|year|unknown`, `source_time_inferred: bool`, `source_time_raw_text: str|None`.
- `SystemTime`: `observed_at`, `fetched_at`, `ingested_at` — обов'язкові, aware UTC; validator відхиляє naive datetime і будь-яке значення поза UTC (нормалізує або відхиляє — обрати відхилення, щоб не маскувати помилку).
- `EffectiveTime` для аналітики: `effective_at`, `effective_at_basis = source_event|source_updated|observed`, `source_time_inferred=True` коли basis ≠ source_event. Функція `derive_effective_time()` з тестами; заборонено писати `fetched_at` у `source_event_at` (тест-регресія R-43).
- `BitemporalInterval`: `valid_from/valid_to`, `known_from/known_to` (напіввідкриті, `None` = відкрито) і чиста функція `build_intervals(versions)` для послідовності версій одної сутності; тести: late arrival, backdated correction, relisting — обидві осі окремі.

### 4. Гроші та нормалізація значень (§5.1, §9.3 п.8, §12.2)

- `Money(amount_minor: int, currency: str[3])` — без float; validator ISO-4217 формату (не повний список).
- `ContactValue(kind: phone|email|url|messenger|other, raw: str, normalized: str|None)`: телефон → E.164 (`phonenumbers`, default region UA), e-mail → lowercase + IDNA домен; raw ніколи не втрачається.
- `MeasuredValue(value, unit, raw_value, raw_unit)` для SI-конверсії.

### 5. Artifact contracts (§7.3, §9.1, §10 п.5, п.7) — R-27, R-38, R-41

- `ArtifactRef(uri, sha256, size_bytes, media_type, schema_version)`; `RawArtifactRef` (+ HTTP metadata), `NormalizedArtifactRef` (+ `entity_uuid`, `domain`, `parser_version`, raw/fetch lineage).
- `UploadClaim(object_key, owner, status, lease_expires_at, claim_generation: int)` і commit predicate як чиста функція `can_commit(claim, now, generation)`.

### 6. Projection command / receipt / acknowledgement / domain event (§7.3 кроки 2–4, §9.2 receipt) — R-30, R-36, R-37, R-42

- `ProjectionCommand` (`task_id`, `entity_uuid`, `projection_version`, `target_collection`, `schema_version`, `artifact: NormalizedArtifactRef`, `priority`, `not_before`).
- `AppliedProjectionReceipt` (`projection_task_id`, `entity_uuid`, `projection_version`, `target_collection`, `document_id`, `applied_to_current`, `state_changed`, `previous_version/hash`, `result_version/hash`, `event_bytes: bytes|None`, `event_media_type`, `event_sha256`, `event_artifact: ArtifactRef|None`, `committed_at`, `cluster_time`).
- `ProjectionAcknowledgement` (`task_id`, entity/version, receipt id/cluster time, `applied_to_current`, `state_changed`, `acknowledged_at`, result/event hashes).
- `DomainChangedEvent` (`event_id`, `aggregate_id`, `aggregate_version`, `event_type`, `payload_schema_version`, `occurred_at`, payload або artifact ref) + **canonical serialization**: детермінований UTF-8 JSON (sorted keys, без пробілів, фіксований формат datetime/UUID/decimal), функція `encode_event(event) -> (bytes, media_type, sha256)`; тест byte-equivalence при повторній серіалізації і після round-trip; ліміт inline 256 KiB → інакше `event_artifact`.
- Правило `should_emit_domain_changed(receipt) = applied_to_current and state_changed` — тест.

### 7. Мінімальний contract current document (§9.2)

Pydantic-модель `CurrentDocumentBase` за YAML §9.2 (`_id`, `schema_version`, `entity_kind`, `source`, `identity_hash`, `projection_version`, `state_hash`, `core`, `attributes`, `latest_state`, `lineage`, `time`, `first_seen_at`, `last_seen_at`) + `EntityKind` enum (`catalog_item|catalog_offer|vehicle_listing|seller`, розширюваний WP-07/09 через dependency). Обчислення `state_hash` — versioned чиста функція над `core+attributes+latest_state` (детермінована, незалежна від порядку полів/локалі — тест §9.4).

### 8. Resolution decision (§9.8) — R-45

`ResolutionDecision(decision_id, decision_version, action: merge|unmerge|reject|manual_link|manual_block, member_entity_ids, canonical_group_id, evidence_refs, feature_values, score, calibration_version, rule_or_model_version, actor, reason, effective_at, recorded_at, supersedes_decision_id)` + чиста функція `project_groups(decisions) -> groups` з тестом «merge → manual_block → unmerge → replay» (тест рівня 10 §16.1 на рівні контракту).

### 9. Dataset release manifest (§9.9) — R-46

`ReleaseManifest` з усіма полями §9.9 (release_id, tag, часи, owner, purpose, watermark, entity-version list або partition index hash, registry/policy versions, included/excluded/degraded sources, schema/parser/normalizer/matcher/resolution/translation versions, git commit, image digests, config hash, build command, parts[], quality report, reconciliation result, previous/superseding), `ReleasePart(uri, format, partition, row_count, min/max effective/system time, size, sha256)`, `ReleaseState` enum (`draft|building|validating|published|failed|superseded`) з дозволеними переходами (функція + тест, що `published` immutable).

### 10. Схеми, версіонування, сумісність (§9.4)

- Кожна модель має `schema_version` (semver-подібно `major.minor`); `uv run collector contracts export` (нова CLI-команда — owned: `src/collector/cli.py` розширюється **лише** subcommand `contracts`) генерує JSON Schema у `schemas/{events,mongo,releases}/<name>.v<major>.json`; CI-тест, що snapshot у репозиторії збігається зі згенерованим (drift = fail).
- Compatibility test: fixture-документи попередньої minor-версії валідуються новою моделлю; тест, що видалення/перейменування поля без підвищення major падає (перевірка через збережені snapshot-и: diff required/props).
- `docs/contracts.md`: як додати optional поле (minor), як зробити breaking change (major + migration/reprojection plan + fixture + compatibility test), ownership і процедура dependency-запиту.

## Команди перевірки

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -m "not live"
uv run collector contracts export --check
```

## Acceptance

- всі команди зелені; `mypy --strict` без ignore;
- snapshot JSON Schema у `schemas/**` для кожної публічної моделі; `--check` не знаходить drift;
- тести: identity hash golden; усі 70 `source_id` валідні; п'ять enum §5.5 з точними значеннями + mapping research; naive datetime відхиляється; `fetched_at` не потрапляє у `source_event_at`; bitemporal інтервали для late arrival/backdated/relisting; `encode_event` byte-equivalent і ліміт 256 KiB; `state_hash` незалежний від порядку полів; resolution replay; release state machine; compatibility fixtures;
- жодного I/O у `src/collector/contracts/**` (тест: імпорт модуля не тягне httpx/sqlalchemy/pymongo);
- `docs/contracts.md` описує процедуру змін і ownership.

## Rollback/disable

Контракти без runtime-ефекту до появи споживачів; відкат — revert PR до злиття WP-01A/01B.

## Docs (етап 5)

`docs/contracts.md` (перевірити повноту), docstrings усіх публічних моделей, `schemas/README.md`, ADR-0003 «Canonical event serialization» (формат bytes, чому sorted-keys JSON, ліміт 256 KiB).
