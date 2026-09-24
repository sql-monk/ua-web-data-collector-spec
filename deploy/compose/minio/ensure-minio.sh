#!/bin/sh
# One-shot `ensure-minio` (WP-00 PR5, §13 «доступ розділений на writer/parser/auditor roles»):
# ідемпотентно створює buckets і per-component користувачів MinIO з policies з ./policies.
#
# Виконується в тому самому source-built image, що й сервер `minio` (pinned `mc` збирається
# разом із ним), від
# non-root uid з read-only rootfs; конфіг mc — лише у tmpfs. У image немає sed/grep/awk, тому
# розбір файлів — вбудованими засобами bash.
#
# Секрети (Docker secrets, /run/secrets):
#   minio_root_user, minio_root_password — root; монтує ЛИШЕ цей one-shot (і сервер minio);
#   minio_<component>                    — `access_key=<ім'я>` і `secret_key=<hex>` (два рядки,
#                                          формат — deploy/compose/README.md «MinIO»).
# Жоден секрет не йде в argv (його видно в `docker top`/`/proc`) і не друкується: root — через
# змінну MC_HOST_<alias> лише в цьому процесі, secret key користувача — через stdin
# `mc admin user add`.
#
# Повторний запуск безпечний: `mc mb --ignore-existing`; `policy create` перезаписує policy тим
# самим JSON; `user add` для наявного користувача оновлює secret key (ротація = новий файл
# секрету + повторний `up`); `policy attach` уже прикріпленої policy — rc 0. Після attach
# перевіряється, що в користувача рівно одна policy — власна; інакше exit 1 (least privilege:
# зайву policy, додану вручну, мовчки не лишаємо).
set -eu

secrets_dir="${COLLECTOR_MINIO_SECRETS_DIR:-/run/secrets}"
policies_dir="${COLLECTOR_MINIO_POLICIES_DIR:-/etc/collector/minio/policies}"
endpoint="${COLLECTOR_MINIO_URL:-http://minio:9000}"
alias_name=collector

# Списки без shell arrays: runtime image має POSIX BusyBox `sh`, додатковий bash не потрібен.
# Імена ключів у bucket-і — конвенція WP-02 `collector.storage`.
buckets="raw normalized archive translated events"
# Компонент → policies/<component>.json → користувач `collector-<component>`.
components="fetcher parser projector translation maintenance readonly"

die() { echo "ensure-minio: error: $*" >&2; exit 1; }

# Перший рядок файла без CR/LF; порожній або відсутній файл — помилка з іменем, без вмісту.
read_first_line() {
  local line=""
  [ -f "$1" ] || die "немає секрету $1 (запустіть deploy/compose/secrets/init-secrets.sh)"
  IFS= read -r line < "$1" || true
  line="${line%$'\r'}"
  [ -n "$line" ] || die "секрет $1 порожній"
  printf '%s' "$line"
}

root_user="$(read_first_line "$secrets_dir/minio_root_user")"
root_password="$(read_first_line "$secrets_dir/minio_root_password")"
# Обидва значення стають частиною URL у MC_HOST_*: лише URL-безпечні символи.
[ "${#root_user}" -ge 3 ] || die "minio_root_user має недопустимий формат"
[ "${#root_password}" -ge 8 ] || die "minio_root_password має недопустимий формат"
case "$root_user" in *[!A-Za-z0-9._-]*) die "minio_root_user має недопустимий формат" ;; esac
case "$root_password" in
  *[!A-Za-z0-9._-]*) die "minio_root_password має недопустимий формат" ;;
esac
export "MC_HOST_${alias_name}=${endpoint%%://*}://${root_user}:${root_password}@${endpoint#*://}"
unset root_password

timeout 60 mc ready "$alias_name" >/dev/null

for bucket in $buckets; do
  mc mb --ignore-existing "$alias_name/$bucket" >/dev/null
  echo "ensure-minio: bucket $bucket ok"
done

for component in $components; do
  file="$secrets_dir/minio_$component"
  [ -f "$file" ] || die "немає секрету $file (запустіть deploy/compose/secrets/init-secrets.sh)"
  access_key=""
  secret_key=""
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in
      access_key=*) access_key="${line#access_key=}" ;;
      secret_key=*) secret_key="${line#secret_key=}" ;;
      "") ;;
      *) die "$file: невідомий рядок (очікуються лише access_key=… і secret_key=…)" ;;
    esac
  done < "$file"
  [ "$access_key" = "collector-$component" ] \
    || die "$file: access_key має бути collector-$component"
  [ "${#secret_key}" -eq 40 ] || die "$file: secret_key має бути 40 hex-символів"
  case "$secret_key" in
    *[!0-9a-f]*) die "$file: secret_key має бути 40 hex-символів" ;;
  esac

  policy="collector-$component"
  mc admin policy create "$alias_name" "$policy" "$policies_dir/$component.json" >/dev/null
  printf '%s\n%s\n' "$access_key" "$secret_key" | mc admin user add "$alias_name" >/dev/null
  mc admin policy attach "$alias_name" "$policy" --user "$access_key" >/dev/null
  info="$(mc admin user info "$alias_name" "$access_key" --json)"
  case "$info" in
    *"\"policyName\":\"$policy\""*) ;;
    *) die "$access_key: очікувалась лише policy $policy; зайві policies відкріпіть" \
      "(mc admin policy detach) і повторіть" ;;
  esac
  echo "ensure-minio: user $access_key → policy $policy ok"
done

echo "ensure-minio: done"
