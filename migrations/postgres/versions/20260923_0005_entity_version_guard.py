"""WP-01A PR2 (gate 2, F-1): trigger-guard — версії `entity_index` ніколи не зменшуються.

§9.5: `confirmed_projection_version` змінюється лише через `GREATEST` і ніколи не зменшується;
`projection_version` — лічильник видачі, відкат якого зламав би unique
`(entity_uuid, projection_version)` при наступному `record_parse_result`. Репозиторій це вже
гарантує, але до gate 2 інваріант тримався **лише кодом**: роль із UPDATE на таблицю могла
записати менше значення напряму. Тригер закриває це на рівні БД для всіх ролей, включно з
owner і superuser (тригер — не GRANT). Column-level GRANT — у `sql/roles.sql`.

Downgrade безпечний: прибирає лише тригер і функцію.

Revision ID: 0005_entity_version_guard
Revises: 0004_artifacts_projection
Create Date: 2026-09-23 18:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_entity_version_guard"
down_revision: str | Sequence[str] | None = "0004_artifacts_projection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION entity_index_versions_monotonic() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.projection_version < OLD.projection_version
               OR NEW.confirmed_projection_version < OLD.confirmed_projection_version THEN
                RAISE EXCEPTION
                    'entity_index %: версії не зменшуються (projection % -> %, confirmed % -> %)',
                    OLD.entity_uuid, OLD.projection_version, NEW.projection_version,
                    OLD.confirmed_projection_version, NEW.confirmed_projection_version
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER entity_index_versions_monotonic BEFORE UPDATE ON entity_index "
        "FOR EACH ROW EXECUTE FUNCTION entity_index_versions_monotonic()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS entity_index_versions_monotonic ON entity_index")
    op.execute("DROP FUNCTION IF EXISTS entity_index_versions_monotonic()")
