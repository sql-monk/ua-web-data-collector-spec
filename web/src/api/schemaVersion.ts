/**
 * Версія API/schema, з якою зібрано цей GUI. Заготовка WP-00: реальне значення підставляє
 * генератор OpenAPI-клієнта у WP-11C (див. `src/api/README.md`) з поля `info.version`.
 */
export const API_SCHEMA_VERSION = '0.0.0-placeholder';

/** Базовий шлях API: same-origin (nginx reverse proxy `/api` → `api:8000`, §8, §13). */
export const API_BASE_PATH = '/api/v1';
