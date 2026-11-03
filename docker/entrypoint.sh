#!/usr/bin/env bash
# Default: run API + sync worker in one container. Other args go to `mi-health`.
set -euo pipefail

if [ "$#" -gt 0 ] && [ "$1" != "all" ]; then
    exec mi-health "$@"
fi

port="${MI_HEALTH_PORT:-8330}"
mi-health serve-api --host 0.0.0.0 --port "$port" &
api_pid=$!
mi-health serve-worker &
worker_pid=$!

trap 'kill -TERM "$api_pid" "$worker_pid" 2>/dev/null || true' TERM INT
status=0
wait -n "$api_pid" "$worker_pid" || status=$?
kill -TERM "$api_pid" "$worker_pid" 2>/dev/null || true
wait 2>/dev/null || true
exit "$status"
