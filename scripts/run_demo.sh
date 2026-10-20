#!/bin/sh
set -eu
umask 077
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
demo_host="${DEMO_HOST:-127.0.0.1}"
if [ "$demo_host" != '127.0.0.1' ] && [ -z "${DEMO_PUBLIC_BASE_URL:-}" ]; then
  printf '%s\n' '內網模式請同時設定 DEMO_PUBLIC_BASE_URL，例如 http://192.168.1.100:5001。' >&2
  exit 1
fi
export CHECKIN_ENV=demo
export DATABASE_PATH="$project_dir/instance/demo.sqlite3"
export PUBLIC_BASE_URL="${DEMO_PUBLIC_BASE_URL:-http://127.0.0.1:5001}"
export EVENT_NAME="${DEMO_EVENT_NAME:-導生晚宴（示範）}"
export SECRET_KEY=''
export TRUST_PROXY=0
mkdir -p instance
if [ ! -f instance/demo.sqlite3 ]; then
  .venv/bin/python -m flask --app wsgi seed-demo > instance/demo-credentials.txt
fi
printf '示範帳號資訊：%s/instance/demo-credentials.txt\n' "$project_dir"
printf '示範網址：%s\n' "$PUBLIC_BASE_URL"
exec .venv/bin/python -m flask --app wsgi run --host "$demo_host" --port 5001
