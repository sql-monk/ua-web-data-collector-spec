-- Dev init (docker-entrypoint-initdb.d): лише створення NOLOGIN group-ролей §13.
-- Канонічний повний скрипт (ownership + GRANT після міграцій):
-- src/collector/persistence/postgres/sql/roles.sql, застосовується `collector db roles`.
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
