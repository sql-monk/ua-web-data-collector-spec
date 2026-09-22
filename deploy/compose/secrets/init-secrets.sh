#!/usr/bin/env bash
# Створює локальні файли секретів для docker-compose.yml, якщо їх ще немає.
# Паролі та keyfile генеруються ВИПАДКОВО (gate 3, SEC L-3: жодних default credentials);
# з *.example копіюється лише не-секретне ім'я користувача MinIO. Реальні файли — у .gitignore.
#
# Права файлів: 0644 свідомо. Compose bind-mount-ить file-secrets у /run/secrets/<name> з правами
# ХОСТА, а читають їх non-root uid контейнерів (postgres/mongo 999, collector 10001) — 0600 від
# користувача хоста дав би EACCES на Linux. Це прийняте відхилення для single-host MVP (ADR-0002);
# production — Swarm secrets (Q-013, WP-01D).
#
# Формат: один рядок + LF, без CR (Windows openssl друкує CRLF; entrypoint-и образів обрізають
# лише `\n`, тож `\r` у паролі ламає автентифікацію).
#
# Використання: ./deploy/compose/secrets/init-secrets.sh   (з кореня репозиторію або будь-де)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
umask 022

random_hex() {  # 24 байти → 48 hex-символів
  {
    if command -v openssl >/dev/null 2>&1; then
      openssl rand -hex 24
    elif command -v python3 >/dev/null 2>&1; then
      python3 -c 'import secrets; print(secrets.token_hex(24))'
    else
      head -c 24 /dev/urandom | od -An -tx1
    fi
  } | tr -d ' \r\n'
  echo
}

random_keyfile() {  # MongoDB keyFile: base64 із 756 байтів ентропії, без переносів
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 756
  else
    head -c 756 /dev/urandom | base64
  fi | tr -d ' \r\n'
}

for example in "$here"/*.example; do
  name="$(basename "$example" .example)"
  target="$here/$name"
  if [ -e "$target" ]; then
    echo "skip  $name (exists)"
    continue
  fi
  case "$name" in
    mongo_keyfile)
      random_keyfile > "$target"; echo "gen   $name (random keyfile)" ;;
    *_password)
      random_hex > "$target"; echo "gen   $name (random)" ;;
    *)
      tr -d '\r' < "$example" > "$target"; echo "copy  $name (from example — non-secret)" ;;
  esac
  chmod 0644 "$target"
done
