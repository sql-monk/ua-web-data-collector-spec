-- Dev init (docker-entrypoint-initdb.d, WP-00 PR4): звузити кластерні default-права PUBLIC.
--
-- За замовчуванням PostgreSQL дає PUBLIC (тобто КОЖНІЙ ролі) CONNECT і TEMPORARY на кожну БД
-- кластера. Runtime-ролі §13 призначені рівно для однієї БД застосунку, тому:
--   * БД застосунку (current_database(), тобто POSTGRES_DB, типово `collector`): REVOKE
--     CONNECT, TEMPORARY FROM PUBLIC і явний GRANT CONNECT group-ролям §13 (01-roles.sql
--     виконується раніше за алфавітом, тож ролі вже існують). TEMPORARY не видається нікому:
--     застосунок не створює тимчасових таблиць;
--   * службові `postgres` і `template1`: REVOKE ALL FROM PUBLIC. До них підключається лише
--     superuser (entrypoint, `psql` оператора), а superuser перевірку прав CONNECT оминає;
--     healthcheck `pg_isready` не автентифікується. `template0` і так `datallowconn = false`.
--
-- Лише права на рівні БД — жодних GRANT на таблиці/схеми, паролів чи LOGIN (їх видає
-- `collector db roles --with-login` після міграцій). Виконується тільки на порожньому data
-- directory (перший старт кластера); для наявного кластера — див. README.md поруч.
DO $$
DECLARE
    db_name text;
    role_name text;
BEGIN
    FOREACH db_name IN ARRAY ARRAY['postgres', 'template1'] LOOP
        IF db_name <> current_database()
            AND EXISTS (SELECT 1 FROM pg_database WHERE datname = db_name) THEN
            EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC', db_name);
        END IF;
    END LOOP;

    EXECUTE format('REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
    FOREACH role_name IN ARRAY ARRAY[
        'collector_migrate', 'collector_scheduler', 'collector_fetcher', 'collector_parser',
        'collector_projector', 'collector_translation', 'collector_api_ro', 'collector_export_ro'
    ] LOOP
        EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), role_name);
    END LOOP;
END
$$;
