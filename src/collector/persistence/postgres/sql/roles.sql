-- Ролі БД §13 / картка WP-01A (PR1 + PR2). Ідемпотентно; виконується `collector db roles`
-- ПІСЛЯ `collector db migrate` (GRANT потребує таблиць) і повторюється після кожної міграції.
--
-- Скрипт створює group-ролі NOLOGIN і видає GRANT; атрибут LOGIN і паролі він НЕ змінює
-- (жодного secret у репозиторії). LOGIN runtime-ролей вмикає `collector db roles --with-login`
-- (PR2, `roles.apply_logins`): пароль кожної ролі береться з її DSN-секрету
-- `postgres_dsn_<component>`, у БД іде лише SCRAM verifier. Повторний запуск цього скрипта
-- без `--with-login` уже видані логіни не вимикає. `collector_migrate` LOGIN не отримує:
-- міграції виконує окремий login (у dev — superuser `POSTGRES_USER`).
--
-- | Роль                  | Компонент                       | Права                                        |
-- |-----------------------|---------------------------------|----------------------------------------------|
-- | collector_migrate     | лише міграції (owner об'єктів)  | owner усіх таблиць/функцій; не runtime       |
-- | collector_scheduler   | scheduler + controller          | control plane/queue/limiter/pools/audit RW;  |
-- |                       | + maintenance + outbox publisher| recover leases, expire claims, publish outbox|
-- | collector_fetcher     | discovery/fetch/browser workers | queue, permits, routes/cursors, fetches,     |
-- |                       |                                 | raw_objects, upload claims                   |
-- | collector_parser      | parse workers                   | queue, upload claims, parse_attempts,        |
-- |                       |                                 | normalized_artifacts, entity_index, tasks,   |
-- |                       |                                 | outbox (лише INSERT) — §13 «pointer/task/outbox»|
-- | collector_projector   | projector workers               | claim/ack projection_tasks, entity_index     |
-- |                       |                                 | confirmed version, change_events, outbox     |
-- | collector_translation | translation workers             | queue claim; PR3: news/translations          |
-- | collector_api_ro      | operator/read API               | SELECT усіх таблиць                          |
-- | collector_export_ro   | exporter                        | SELECT усіх таблиць                          |
--
-- Спільна база кожного worker-а (runtime WP-01D, dependency WP-01D→WP-01A §2.3): claim/heartbeat/
-- complete/retry `crawl_jobs` (SELECT, UPDATE), `dead_letters` (INSERT), bootstrap
-- `worker_pools` (SELECT, INSERT), `worker_instances` (SELECT, INSERT, UPDATE) і `audit_log`
-- (лише INSERT: bootstrap `upsert_pool` та control-plane операції пишуть audit у своїй
-- транзакції, PR2). Runtime-ролі не мають UPDATE/DELETE на audit_log (плюс тригер
-- append-only), не мають SELECT на audit_log (крім scheduler/ro) і не мають DDL:
-- alembic_version доступна лише collector_migrate.

DO $$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY[
        'collector_migrate', 'collector_scheduler', 'collector_fetcher', 'collector_parser',
        'collector_projector', 'collector_translation', 'collector_api_ro', 'collector_export_ro'
    ] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN', role_name);
        END IF;
    END LOOP;
END
$$;

-- Ownership → collector_migrate (лише якщо поточний користувач має на це право).
DO $$
DECLARE
    rel record;
BEGIN
    IF NOT (pg_has_role(current_user, 'collector_migrate', 'MEMBER')
            OR (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)) THEN
        RAISE NOTICE 'ownership не змінено: % не є членом collector_migrate', current_user;
        RETURN;
    END IF;
    FOR rel IN
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
    LOOP
        EXECUTE format('ALTER TABLE public.%I OWNER TO collector_migrate', rel.relname);
    END LOOP;
    IF to_regprocedure('public.audit_log_append_only()') IS NOT NULL THEN
        EXECUTE 'ALTER FUNCTION public.audit_log_append_only() OWNER TO collector_migrate';
    END IF;
    IF to_regprocedure('public.entity_index_versions_monotonic()') IS NOT NULL THEN
        EXECUTE 'ALTER FUNCTION public.entity_index_versions_monotonic() OWNER TO collector_migrate';
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO collector_scheduler, collector_fetcher, collector_parser,
    collector_projector, collector_translation, collector_api_ro, collector_export_ro;

-- Read-only ролі: SELECT усіх таблиць (крім alembic_version).
GRANT SELECT ON sources, source_policy_versions, source_routes, source_cursors, crawl_runs,
    crawl_jobs, dead_letters, origin_rate_buckets, origin_rate_permits, worker_pools,
    worker_instances, scale_commands, audit_log,
    fetches, raw_objects, parse_attempts, artifact_upload_claims, normalized_artifacts,
    projection_tasks, projection_acknowledgements, entity_index, change_events, outbox_events
    TO collector_api_ro, collector_export_ro;

-- Спільна база всіх worker-ролей (див. заголовок).
GRANT SELECT, UPDATE ON crawl_jobs TO collector_fetcher, collector_parser,
    collector_projector, collector_translation;
GRANT SELECT, INSERT ON dead_letters, worker_pools TO collector_fetcher, collector_parser,
    collector_projector, collector_translation;
GRANT SELECT, INSERT, UPDATE ON worker_instances TO collector_fetcher, collector_parser,
    collector_projector, collector_translation;
GRANT INSERT ON audit_log TO collector_fetcher, collector_parser, collector_projector,
    collector_translation;

-- scheduler + controller + maintenance: control plane, черга, limiter конфіг, pools/scale,
-- audit; PR2: recover projection leases, expire upload claims, outbox publisher.
GRANT SELECT, INSERT, UPDATE ON sources, source_policy_versions, source_routes, source_cursors,
    crawl_runs, crawl_jobs, origin_rate_buckets, worker_pools, worker_instances, scale_commands
    TO collector_scheduler;
GRANT SELECT, INSERT ON dead_letters, audit_log TO collector_scheduler;
GRANT SELECT, UPDATE ON origin_rate_permits TO collector_scheduler;
GRANT UPDATE ON dead_letters TO collector_scheduler;  -- resolved_at/resolution оператором
GRANT SELECT ON fetches, raw_objects, parse_attempts, normalized_artifacts,
    projection_acknowledgements, entity_index, change_events TO collector_scheduler;
-- Column-level (gate 3, S-3): лише колонки, які пишуть recover/quarantine projection tasks,
-- outbox publisher (lease/published/failed/parked/unpark) і sweeper/expire upload claims.
-- Scheduler не може переписати payload, topic, версію чи fencing-посилання (sha256/uri).
-- REVOKE табличного UPDATE спершу — повторний `db roles` на БД до gate 3 інакше лишив би його.
REVOKE UPDATE ON artifact_upload_claims, projection_tasks, outbox_events FROM collector_scheduler;
GRANT SELECT ON artifact_upload_claims, projection_tasks, outbox_events TO collector_scheduler;
GRANT UPDATE (status, lease_owner, lease_expires_at, leased_at, finished_at, last_error_code,
    last_error_message, updated_at) ON projection_tasks TO collector_scheduler;
GRANT UPDATE (available_at, published_at, parked_at, attempts, last_error_code,
    last_error_message, updated_at) ON outbox_events TO collector_scheduler;
GRANT INSERT (claim_id, object_key, owner, status, claim_generation, acquired_at,
    lease_expires_at, media_type, created_at, updated_at) ON artifact_upload_claims
    TO collector_scheduler;
GRANT UPDATE (owner, status, claim_generation, acquired_at, lease_expires_at, released_at,
    media_type, updated_at) ON artifact_upload_claims TO collector_scheduler;

-- discovery/fetch/browser: enqueue наступних jobs, permits, circuit breaker маршрутів,
-- cursors; PR2: fetch lineage, raw pointers, upload claims (§10 п.5).
GRANT SELECT ON sources, source_policy_versions, crawl_runs TO collector_fetcher;
GRANT INSERT ON crawl_jobs TO collector_fetcher;
GRANT SELECT, INSERT, UPDATE ON origin_rate_permits, source_cursors TO collector_fetcher;
GRANT SELECT, INSERT, UPDATE ON source_routes TO collector_fetcher;
GRANT SELECT, UPDATE ON origin_rate_buckets TO collector_fetcher;
GRANT SELECT, INSERT ON fetches, raw_objects TO collector_fetcher;
GRANT SELECT, INSERT, UPDATE ON artifact_upload_claims TO collector_fetcher;

-- parse (§13: «parser пише лише artifact pointer/task/outbox»): upload claims normalized
-- artifact-ів, parse_attempts, normalized_artifacts, видача projection_version під row lock
-- на entity_index (FOR UPDATE вимагає UPDATE), projection_tasks, outbox (projection.command).
GRANT SELECT ON sources, fetches, raw_objects TO collector_parser;
GRANT INSERT ON crawl_jobs TO collector_parser;
GRANT SELECT, INSERT, UPDATE ON artifact_upload_claims TO collector_parser;
-- normalized_artifacts (gate 3, CR-6/S-2): закомічений pointer незмінний — parser лише
-- проставляє lineage `parse_attempt_id` новому рядку.
REVOKE UPDATE ON normalized_artifacts FROM collector_parser;
GRANT SELECT, INSERT ON normalized_artifacts TO collector_parser;
GRANT UPDATE (parse_attempt_id) ON normalized_artifacts TO collector_parser;
GRANT SELECT, INSERT ON parse_attempts, projection_tasks, outbox_events TO collector_parser;
-- entity_index (gate 2, F-1): лише колонки, які parser справді пише (видача версії). REVOKE
-- табличного UPDATE спершу — інакше повторний запуск на БД після PR2 до gate 2 лишив би його
-- (column GRANT не звужує табличний). Зменшення версій забороняє ще й тригер
-- `entity_index_versions_monotonic` (міграція 0005) — для будь-якої ролі.
-- INSERT теж column-level (gate 3, S-1): лише identity-колонки; версії, `confirmed_at` і
-- `mongo_*` беруться з DB defaults, тож «підтверджену» версію без ack вставити не можна.
REVOKE UPDATE, INSERT ON entity_index FROM collector_parser, collector_projector;
GRANT SELECT ON entity_index TO collector_parser;
GRANT INSERT (entity_uuid, domain, entity_kind, source_id, source_item_id, identity_hash,
    canonical_url, created_at, updated_at) ON entity_index TO collector_parser;
GRANT UPDATE (projection_version, updated_at) ON entity_index TO collector_parser;

-- projector (§7.3 п.4): claim/heartbeat/ack projection_tasks, монотонний confirmed version
-- в entity_index, change_events + outbox(domain.changed). normalized_artifacts — лише
-- читання pointer-а; domain payload projector пише у MongoDB, не сюди.
GRANT SELECT ON normalized_artifacts TO collector_projector;
GRANT SELECT, UPDATE ON projection_tasks TO collector_projector;
GRANT SELECT ON entity_index TO collector_projector;
GRANT UPDATE (confirmed_projection_version, confirmed_at, mongo_collection, mongo_document_id,
    updated_at) ON entity_index TO collector_projector;
GRANT SELECT, INSERT ON projection_acknowledgements, change_events, outbox_events
    TO collector_projector;

-- outbox_events (gate 2, F-2): parser створює лише внутрішню `projection.command`
-- (topic='internal'); `domain.changed` (topic='domain') з'являється тільки в ack-транзакції
-- projector-а (§7.3 п.4). Row Level Security закриває підробку `domain.changed` parser-ом на
-- рівні БД. Owner (collector_migrate) і superuser RLS обходять — міграції й maintenance не
-- зачеплені; ролі без політики рядків не бачать (fetcher/translation GRANT і так не мають).
ALTER TABLE outbox_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS outbox_events_all ON outbox_events;
DROP POLICY IF EXISTS outbox_events_parser_select ON outbox_events;
DROP POLICY IF EXISTS outbox_events_parser_insert ON outbox_events;
CREATE POLICY outbox_events_all ON outbox_events FOR ALL
    TO collector_scheduler, collector_projector, collector_api_ro, collector_export_ro
    USING (true) WITH CHECK (true);
CREATE POLICY outbox_events_parser_select ON outbox_events FOR SELECT
    TO collector_parser USING (topic = 'internal');
CREATE POLICY outbox_events_parser_insert ON outbox_events FOR INSERT
    TO collector_parser WITH CHECK (topic = 'internal');
