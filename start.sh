#!/bin/sh
set -e

PORT="${PORT:-8080}"
echo "Starting app on port $PORT"
exec gunicorn --bind "0.0.0.0:$PORT" app:app
