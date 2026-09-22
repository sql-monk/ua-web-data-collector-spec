#!/usr/bin/env bash
# Створює локальні файли секретів для docker-compose.yml з *.example, якщо їх ще немає.
# Реальні файли — у .gitignore; для чогось, крім локальної розробки, замініть значення.
# Keyfile MongoDB генерується випадково (openssl rand), а не копіюється з прикладу.
#
# Використання: ./deploy/compose/secrets/init-secrets.sh   (з кореня репозиторію або будь-де)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
umask 022  # контейнери читають секрети від non-root uid (10001/999): файли мають бути readable

for example in "$here"/*.example; do
  name="$(basename "$example" .example)"
  target="$here/$name"
  if [ -e "$target" ]; then
    echo "skip  $name (exists)"
    continue
  fi
  if [ "$name" = "mongo_keyfile" ]; then
    if command -v openssl >/dev/null 2>&1; then
      openssl rand -base64 756 | tr -d '\n' > "$target"
    else
      # Fallback без openssl: 756 випадкових байтів у base64 (той самий формат).
      head -c 756 /dev/urandom | base64 | tr -d '\n' > "$target"
    fi
    echo "gen   $name (random keyfile)"
  else
    cp "$example" "$target"
    echo "copy  $name (from example — change for non-local use)"
  fi
  chmod 0644 "$target"
done
