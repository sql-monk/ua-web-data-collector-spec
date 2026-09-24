# WP-00 PR5 — security review

Дата: 2026-09-24. Обсяг: secrets, MinIO/Mongo initialization, Compose mounts.

## Знахідки

Critical/high/medium/low знахідок немає.

## Перевірено

- Least privilege: policies дослівно відповідають таблиці PR5; wildcard actions відсутні.
- Root credentials монтуються лише server + init one-shot.
- Component credentials не передаються через argv або Compose environment.
- `ensure-minio`: non-root, read-only rootfs, tmpfs, `cap_drop: ALL`,
  `no-new-privileges`, без published ports.
- Зайва policy на користувачі спричиняє fail-closed, а не мовчазне розширення прав.
- Некоректні/відсутні secrets спричиняють exit 1 без друку значень.
- Перевірка живого стеку не знайшла високоентропійних значень у container env/commands або
  compose logs.
- Translation credential порожній, provider default `disabled`; credential монтує лише
  translation worker.

## Прийняті межі

- Файлові dev secrets і MinIO root існують у локальному single-host threat model ADR-0002.
- Object lock, encryption/KMS і централізований deletion audit — WP-12/WP-13.

## Вердикт

**approve**.
