#!/usr/bin/env bash
# Створює локальні файли секретів для docker-compose.yml, якщо їх ще немає.
# Паролі та keyfile генеруються ВИПАДКОВО (gate 3, SEC L-3: жодних default credentials);
# з *.example копіюється лише не-секретне ім'я користувача MinIO. Реальні файли — у .gitignore.
# DSN: `postgres_dsn` — міграційний (superuser POSTGRES_USER, пароль = postgres_password);
# `postgres_dsn_<component>` — сім runtime-ролей §13, кожна з власним паролем (WP-00 PR4).
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

# Чи є на місці секрету готовий файл (gate 2 WP-00 PR4, F-1). Compose bind-mount-ить
# file-secret, і якщо файла немає, Docker Desktop створює на його місці ПОРОЖНІЙ КАТАЛОГ —
# колишній `[ -e ]` вважав його секретом («skip»), і секрет не генерувався ніколи.
#   непорожній файл   → 0 (секрет є, не чіпаємо — ідемпотентність);
#   порожній файл     → 1 (секрету в ньому немає, губити нічого; генеруємо заново);
#   порожній каталог  → прибираємо (артефакт Docker, даних у ньому немає) → 1;
#   непорожній каталог чи інший тип → зупинка з підказкою: вгадувати, що там, не беремось.
secret_present() {
  if [ -d "$1" ]; then
    if rmdir "$1" 2>/dev/null; then
      echo "fix   $(basename "$1") (порожній каталог на місці секрету — прибрано)"
      return 1
    fi
    echo "error: $1 — каталог, а не файл секрету (Docker створює його, якщо запустити" \
      "compose до init-secrets.sh). Видаліть його (rm -r) і запустіть скрипт знову." >&2
    exit 1
  fi
  if [ -e "$1" ] && [ ! -f "$1" ]; then
    echo "error: $1 існує, але не є звичайним файлом; видаліть його і запустіть скрипт знову." >&2
    exit 1
  fi
  [ -s "$1" ]
}

for example in "$here"/*.example; do
  name="$(basename "$example" .example)"
  target="$here/$name"
  if secret_present "$target"; then
    echo "skip  $name (exists)"
    continue
  fi
  case "$name" in
    mongo_keyfile)
      random_keyfile > "$target"; echo "gen   $name (random keyfile)" ;;
    *_password)
      random_hex > "$target"; echo "gen   $name (random)" ;;
    postgres_dsn)
      # DSN міграційної ролі будується з уже згенерованого postgres_password (той самий
      # пароль, що його читає Postgres із POSTGRES_PASSWORD_FILE).
      if ! secret_present "$here/postgres_password"; then
        random_hex > "$here/postgres_password"
        chmod 0644 "$here/postgres_password"
        echo "gen   postgres_password (random, для DSN)"
      fi
      printf 'postgresql://%s:%s@%s:%s/%s\n' \
        "${POSTGRES_USER:-collector}" "$(cat "$here/postgres_password")" \
        "${POSTGRES_HOST:-postgres}" "${POSTGRES_PORT:-5432}" "${POSTGRES_DB:-collector}" \
        > "$target"
      echo "gen   $name (з postgres_password)" ;;
    postgres_dsn_*)
      # DSN runtime-ролі §13 (WP-01A PR2 `collector db roles --with-login`): користувач —
      # `collector_<component>`, пароль — ВЛАСНИЙ випадковий hex (printable ASCII, як вимагає
      # SCRAM verifier без SASLprep). Окремого файла пароля немає: джерело істини — сам DSN,
      # `migrate-postgres` читає з нього пароль і ставить ролі verifier, а сервіс компонента
      # монтує той самий файл як COLLECTOR_POSTGRES_DSN_FILE.
      component="${name#postgres_dsn_}"
      printf 'postgresql://collector_%s:%s@%s:%s/%s\n' \
        "$component" "$(random_hex)" \
        "${POSTGRES_HOST:-postgres}" "${POSTGRES_PORT:-5432}" "${POSTGRES_DB:-collector}" \
        > "$target"
      echo "gen   $name (random, роль collector_$component)" ;;
    *)
      tr -d '\r' < "$example" > "$target"; echo "copy  $name (from example — non-secret)" ;;
  esac
  chmod 0644 "$target"
done
