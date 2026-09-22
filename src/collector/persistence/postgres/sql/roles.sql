-- Ролі БД §13 / картка WP-01A (PR1). Ідемпотентно; виконується `collector db roles` ПІСЛЯ
-- `collector db migrate` (GRANT потребує таблиць) і повторюється після кожної міграції.
--
-- Усі ролі — NOLOGIN group roles без паролів (жодного secret у репозиторії). Оператор створює
-- login-користувачів як членів:  CREATE ROLE fetch_01 LOGIN PASSWORD '...' IN ROLE collector_fetcher;
-- login для міграцій — член collector_migrate (або superuser у dev).
--
-- | Роль                  | Компонент                       | PR1 права                                   |
-- |-----------------------|---------------------------------|---------------------------------------------|
-- | collector_migrate     | лише міграції (owner об'єктів)  | owner усіх таблиць/функцій; не runtime      |
-- | collector_scheduler   | scheduler + controller          | control plane/queue/limiter/pools/audit RW  |
-- | collector_fetcher     | discovery/fetch/browser workers | queue claim/complete, permits, routes/cursors|
-- | collector_parser      | parse workers                   | queue claim/complete (parse), instances     |
-- | collector_projector   | projector workers               | instances; PR2: projection tasks/acks       |
-- | collector_translation | translation workers             | instances; PR3: news/translations           |
-- | collector_api_ro      | operator/read API               | SELECT усіх таблиць                         |
-- | collector_export_ro   | exporter                        | SELECT усіх таблиць                         |
--
-- Runtime-ролі не мають UPDATE/DELETE на audit_log (плюс тригер append-only) і не мають
-- DDL: alembic_version доступна лише collector_migrate.

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
END
$$;

GRANT USAGE ON SCHEMA public TO collector_scheduler, collector_fetcher, collector_parser,
    collector_projector, collector_translation, collector_api_ro, collector_export_ro;

-- Read-only ролі: SELECT усіх таблиць (крім alembic_version).
GRANT SELECT ON sources, source_policy_versions, source_routes, source_cursors, crawl_runs,
    crawl_jobs, dead_letters, origin_rate_buckets, origin_rate_permits, worker_pools,
    worker_instances, scale_commands, audit_log
    TO collector_api_ro, collector_export_ro;

-- scheduler + controller: control plane, черга, limiter конфіг, pools/scale, audit.
GRANT SELECT, INSERT, UPDATE ON sources, source_policy_versions, source_routes, source_cursors,
    crawl_runs, crawl_jobs, origin_rate_buckets, worker_pools, worker_instances, scale_commands
    TO collector_scheduler;
GRANT SELECT, INSERT ON dead_letters, audit_log TO collector_scheduler;
GRANT SELECT, UPDATE ON origin_rate_permits TO collector_scheduler;
GRANT UPDATE ON dead_letters TO collector_scheduler;  -- resolved_at/resolution оператором

-- discovery/fetch/browser: claim/heartbeat/complete/retry, enqueue наступних jobs, permits,
-- circuit breaker маршрутів, cursors, heartbeat instance.
GRANT SELECT ON sources, source_policy_versions, crawl_runs, worker_pools TO collector_fetcher;
GRANT SELECT, INSERT, UPDATE ON crawl_jobs, origin_rate_permits, source_cursors, worker_instances
    TO collector_fetcher;
GRANT SELECT, UPDATE ON origin_rate_buckets, source_routes TO collector_fetcher;
GRANT SELECT, INSERT ON dead_letters TO collector_fetcher;

-- parse: parse-jobs у черзі, instance heartbeat; PR2 додасть artifact/projection/outbox.
GRANT SELECT ON sources, worker_pools TO collector_parser;
GRANT SELECT, INSERT, UPDATE ON crawl_jobs, worker_instances TO collector_parser;
GRANT SELECT, INSERT ON dead_letters TO collector_parser;

-- projector / translation: лише власний instance heartbeat у PR1.
GRANT SELECT ON worker_pools TO collector_projector, collector_translation;
GRANT SELECT, INSERT, UPDATE ON worker_instances TO collector_projector, collector_translation;
