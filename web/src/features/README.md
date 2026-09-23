# `src/features` — екрани operator GUI

Порожньо за задумом (WP-00 PR3 — лише каркас, FR-034).

Кожен екран §7.7 отримує тут власний каталог з query/mutation hooks, таблицями з
server-side cursor pagination і SSE-підписками; спільні примітиви — у `src/components`,
транспорт і типи — у `src/api`. Owner — **WP-11C**.

Правило розділення: `routes/` містить лише маршрут і його lazy-межу chunk-а, `features/` —
логіку екрана. Так route-level code splitting лишається справжнім: додавання екрана не
збільшує початковий bundle.
