#!/usr/bin/env bash
# Створює локальні файли секретів для docker-compose.yml, якщо їх ще немає.
# Паролі та keyfile генеруються ВИПАДКОВО (gate 3, SEC L-3: жодних default credentials);
# з *.example копіюється лише не-секретне ім'я користувача MinIO. Реальні файли — у .gitignore.
# DSN: `postgres_dsn` — міграційний (superuser POSTGRES_USER, пароль = postgres_password);
# `postgres_dsn_<component>` — сім runtime-ролей §13, кожна з власним паролем (WP-00 PR4).
# WP-00 PR5 (§13 «облікові дані за компонентами» для object store і MongoDB):
#   `minio_<component>`     — `access_key=collector-<component>` + `secret_key=<40 hex>`;
#   `mongo_uri_<component>` — URI користувача `collector_<component>` з власним паролем;
#   `google_translation_credentials` — зовнішній credential оператора: НЕ генерується, лише
#     створюється порожній файл (інакше `docker compose up` падає на відсутньому file-secret).
# Невідомий `*.example` — зупинка до будь-якого запису (fail-closed: раніше будь-який новий
# приклад мовчки копіювався як «секрет»).
#
# Права файлів: 0644 свідомо. Compose bind-mount-ить file-secrets у /run/secrets/<name> з правами
# ХОСТА, а читають їх non-root uid контейнерів (postgres/mongo 999, collector 10001) — 0600 від
# користувача хоста дав би EACCES на Linux. Це прийняте відхилення для single-host MVP (ADR-0002);
# production — Swarm secrets (Q-013, WP-01D).
#
# Формат: один рядок + LF, без CR (Windows openssl друкує CRLF; entrypoint-и образів обрізають
# лише `\n`, тож `\r` у паролі ламає автентифікацію).
#
# Надійність (gate 3 WP-00 PR4, CR-1..CR-3): кожне згенероване значення перевіряється до запису
# (збій генератора → exit 1 без файла); файл пишеться атомарно (tmp у тому ж каталозі + mv),
# а паралельні запуски серіалізуються lock-каталогом `.init-secrets.lock`.
#
# Використання: ./deploy/compose/secrets/init-secrets.sh   (з кореня репозиторію або будь-де)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
umask 022

die() { echo "error: $*" >&2; exit 1; }

random_hex() {  # $1 байтів (типово 24) → 2×$1 hex-символів (без перевірки — див. new_hex)
  local bytes="${1:-24}"
  {
    if command -v openssl >/dev/null 2>&1; then
      openssl rand -hex "$bytes"
    elif command -v python3 >/dev/null 2>&1; then
      python3 -c "import secrets; print(secrets.token_hex($bytes))"
    else
      head -c "$bytes" /dev/urandom | od -An -tx1
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

# CR-1/L-1: у `$(…)` bash не успадковує errexit, а збій `openssl rand` дає порожній рядок —
# раніше це ставало DSN з порожнім паролем і exit 0. Тепер значення отримується в змінну в
# основному shell і перевіряється за форматом; інакше — exit 1, файл не створюється.
# Результат — у глобальній змінній `value` (без subshell, щоб `die` завершував скрипт).
# $2 — кількість байтів (типово 24 → 48 hex; MinIO secret key — 20 → 40 hex: MinIO історично
# обмежує secret key 40 символами).
new_hex() {
  local bytes="${2:-24}"
  value="$(random_hex "$bytes")"
  [[ "$value" =~ ^[0-9a-f]{$((bytes * 2))}$ ]] || die "генератор випадкових чисел не дав" \
    "$((bytes * 2)) hex-символів для $1 (openssl/python3//dev/urandom); файл не створено"
}

new_keyfile() {
  value="$(random_keyfile)"
  [[ ${#value} -ge 1000 && "$value" =~ ^[A-Za-z0-9+/=]+$ ]] || die "генератор не дав base64 keyfile для $1;" \
    "файл не створено"
}

# CR-3: атомарний запис — tmp у тому ж каталозі (той самий FS) + `mv`. Перерваний запуск не
# лишає напівзаписаного файла, який наступний запуск прийняв би за наявний секрет.
tmp=""
# Вміст передається аргументом (не через pipe): так функція виконується в основному shell, і
# EXIT-trap бачить `tmp`, щоб прибрати його після збою.
write_secret() {  # $1 — шлях, $2 — вміст (із завершальним LF, де потрібно)
  tmp="$(mktemp "$here/.${1##*/}.tmp.XXXXXX")"
  printf '%s' "$2" > "$tmp"
  chmod 0644 "$tmp"
  mv -f "$tmp" "$1"
  tmp=""
}

# CR-3: lock проти паралельних запусків (перевірка «файла немає» і запис мають бути атомарні
# разом, інакше два процеси згенерують різні postgres_password/postgres_dsn). `mkdir` —
# атомарний і переносний (Git Bash, Linux, macOS), на відміну від flock(1). Чекаємо до
# INIT_SECRETS_LOCK_TIMEOUT с (типово 30); lock, що лишився після kill -9, оператор видаляє
# вручну (підказка в помилці і runbook docs/runbooks/clean-host-start.md).
# У lock пишеться PID власника (`$lock/pid`) — лише для діагностики. Автоматично «stale» lock
# не знімається (gate 3' low #3): два процеси, що одночасно визнали lock мертвим, можуть
# зняти вже новий живий lock, а перевірка PID у Git Bash (MSYS PID ≠ Windows PID) ненадійна.
lock="$here/.init-secrets.lock"
lock_timeout="${INIT_SECRETS_LOCK_TIMEOUT:-30}"
waited=0
until mkdir "$lock" 2>/dev/null; do
  if [ "$waited" -ge "$lock_timeout" ]; then
    owner="$(cat "$lock/pid" 2>/dev/null || true)"
    die "інший init-secrets.sh тримає $lock понад $lock_timeout с (PID власника:" \
      "${owner:-невідомо}). Якщо такого процесу немає (ps -p ${owner:-PID}) — видаліть" \
      "каталог (rm -r \"$lock\") і повторіть"
  fi
  sleep 1
  waited=$((waited + 1))
done
trap 'rm -f "$tmp" "$lock/pid"; rmdir "$lock" 2>/dev/null || true' EXIT
echo "$$" > "$lock/pid"

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

# Кожен *.example має відомий тип. Перевірка — ДО будь-якого запису: помилка в імені нового
# прикладу не лишає половину секретів згенерованими, а placeholder не копіюється як секрет.
for example in "$here"/*.example; do
  name="${example##*/}"
  name="${name%.example}"
  case "$name" in
    mongo_keyfile | *_password | postgres_dsn | postgres_dsn_* | minio_root_user) ;;
    minio_fetcher | minio_parser | minio_projector | minio_translation | minio_maintenance) ;;
    minio_readonly | mongo_uri_* | google_translation_credentials) ;;
    *) die "невідомий приклад секрету $name.example: додайте для нього гілку в init-secrets.sh" ;;
  esac
done

for example in "$here"/*.example; do
  name="$(basename "$example" .example)"
  target="$here/$name"
  if [ "$name" = google_translation_credentials ]; then
    # Зовнішній credential оператора (WP-04): вміст не генеруємо і не чіпаємо ніколи. Порожній
    # файл — легальний стан «провайдер не налаштований» (COLLECTOR_TRANSLATION_PROVIDER=disabled);
    # створюється лише тоді, коли файла немає (або на його місці порожній каталог Docker).
    if secret_present "$target"; then
      echo "skip  $name (exists)"
    elif [ -f "$target" ]; then
      echo "keep  $name (порожній: провайдер перекладу не налаштовано)"
    else
      write_secret "$target" ""
      echo "empty $name (порожній файл; credential вписує оператор — deploy/compose/README.md)"
    fi
    continue
  fi
  if secret_present "$target"; then
    echo "skip  $name (exists)"
    continue
  fi
  case "$name" in
    mongo_keyfile)
      new_keyfile "$name"
      write_secret "$target" "$value"; echo "gen   $name (random keyfile)" ;;
    *_password)
      # CR-2: наявний postgres_dsn уже містить пароль міграційної ролі. Новий postgres_password
      # мовчки розійшовся б із ним (auth failure `migrate-postgres`), тому зупинка.
      if [ "$name" = postgres_password ] && secret_present "$here/postgres_dsn"; then
        die "$target відсутній або порожній, а $here/postgres_dsn уже є і містить пароль" \
          "Postgres. Відновіть postgres_password (той самий пароль, що в DSN) або видаліть" \
          "обидва файли разом із томом (docker compose down -v) і повторіть"
      fi
      new_hex "$name"
      write_secret "$target" "$value"$'\n'; echo "gen   $name (random)" ;;
    postgres_dsn)
      # DSN міграційної ролі будується з уже згенерованого postgres_password (той самий
      # пароль, що його читає Postgres із POSTGRES_PASSWORD_FILE).
      if ! secret_present "$here/postgres_password"; then
        new_hex postgres_password
        write_secret "$here/postgres_password" "$value"$'\n'
        echo "gen   postgres_password (random, для DSN)"
      fi
      password="$(tr -d '\r\n' < "$here/postgres_password")"
      # `printf -v` — без subshell (gate 3' low #2: менше fork-ів у Git Bash).
      printf -v dsn 'postgresql://%s:%s@%s:%s/%s\n' \
        "${POSTGRES_USER:-collector}" "$password" \
        "${POSTGRES_HOST:-postgres}" "${POSTGRES_PORT:-5432}" "${POSTGRES_DB:-collector}"
      write_secret "$target" "$dsn"
      echo "gen   $name (з postgres_password)" ;;
    postgres_dsn_*)
      # DSN runtime-ролі §13 (WP-01A PR2 `collector db roles --with-login`): користувач —
      # `collector_<component>`, пароль — ВЛАСНИЙ випадковий hex (printable ASCII, як вимагає
      # SCRAM verifier без SASLprep). Окремого файла пароля немає: джерело істини — сам DSN,
      # `migrate-postgres` читає з нього пароль і ставить ролі verifier, а сервіс компонента
      # монтує той самий файл як COLLECTOR_POSTGRES_DSN_FILE.
      component="${name#postgres_dsn_}"
      new_hex "$name"
      printf -v dsn 'postgresql://collector_%s:%s@%s:%s/%s\n' \
        "$component" "$value" \
        "${POSTGRES_HOST:-postgres}" "${POSTGRES_PORT:-5432}" "${POSTGRES_DB:-collector}"
      write_secret "$target" "$dsn"
      echo "gen   $name (random, роль collector_$component)" ;;
    minio_root_user)
      write_secret "$target" "$(tr -d '\r' < "$example")"$'\n'
      echo "copy  $name (from example — non-secret)" ;;
    minio_*)
      # Per-component користувач MinIO (WP-00 PR5): access key — ім'я компонента (видно в
      # audit-журналі MinIO, хто що видалив), secret key — власний випадковий hex. Користувача,
      # policy і bucket-и створює one-shot `ensure-minio` з цього ж файла.
      component="${name#minio_}"
      new_hex "$name" 20
      write_secret "$target" "access_key=collector-$component"$'\n'"secret_key=$value"$'\n'
      echo "gen   $name (random, користувач collector-$component)" ;;
    mongo_uri_*)
      # URI Mongo-користувача компонента §13 (WP-01B PR1 п.6; користувачів створює
      # `collector db ensure-mongo --users` з паролів у цих файлах). Користувачі — в `admin`.
      component="${name#mongo_uri_}"
      new_hex "$name"
      printf -v uri 'mongodb://collector_%s:%s@%s:%s/%s?replicaSet=rs0&authSource=admin\n' \
        "$component" "$value" \
        "${MONGO_HOST:-mongo}" "${MONGO_PORT:-27017}" "${MONGO_DB:-collector}"
      write_secret "$target" "$uri"
      echo "gen   $name (random, користувач collector_$component)" ;;
    *)
      die "немає гілки для $name" ;;
  esac
done

# CR-2: обидва файли вже були — звіряємо пароль. Скрипт тут нічого не змінює, тому лише
# попередження (пароль у DSN міг бути URL-encoded вручну); розбіжність означатиме auth failure.
dsn_password="$(sed -E 's#^[^:]+://[^:@/]*:([^@]*)@.*#\1#' "$here/postgres_dsn" | tr -d '\r\n')"
if [ "$dsn_password" != "$(tr -d '\r\n' < "$here/postgres_password")" ]; then
  echo "warn: пароль у postgres_dsn не збігається з postgres_password —" \
    "migrate-postgres не автентифікується; узгодьте файли" >&2
fi
