# WP-01B PR1 — security review

Вердикт: **APPROVED**.

- Root Mongo credential доступний лише one-shot `ensure-mongo`; runtime users не мають DDL.
- Projector не має delete; compactor має remove лише на `entity_projection_versions`;
  API/export — find-only; scheduler/fetcher/parser Mongo users не створюються.
- Users URI-secrets читаються all-or-nothing до з'єднання; username та `authSource=admin`
  перевіряються; пароль не потрапляє у `repr`, stdout/stderr або лог.
- Повторний ensure замінює role privileges декларативною матрицею, тому ручне розширення прав
  не закріплюється непомітно.
- Schema/index drift і невідомі validator constructs fail-closed. Автоматичного drop/rebuild
  indexes або collections немає.
- `enableTestCommands=1` є лише в ізольованому CI Mongo container, не в Compose/runtime.
- CI root password і keyfile генеруються на run, mask-яться й не зберігаються в репозиторії.

Залишковий ризик: single-member replica set не має HA; він прийнятий лише для local MVP і
зафіксований ADR-0010. Перед production потрібна multi-member topology та restore drill.
