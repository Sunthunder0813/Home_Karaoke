#!/bin/sh
set -eu

echo "Starting app, PORT=${PORT:-<unset>}"
if [ -z "${PORT:-}" ]; then
  PORT=8080
fi

exec gunicorn --bind 0.0.0.0:${PORT} app:app
