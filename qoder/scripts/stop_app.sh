#!/usr/bin/env bash
# 停止由 scripts/start_app.sh 启动的拾忆应用。记忆在 PowerContext 中，不受影响。
set -euo pipefail
cd "$(dirname "$0")/.."

PIDFILE=var/run/app.pid
if [ ! -f "$PIDFILE" ]; then
  echo "没有 var/run/app.pid：本脚本没有启动过应用，不做任何终止操作。" >&2
  exit 1
fi

PID="$(cat "$PIDFILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  for _ in $(seq 1 40); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.25
  done
  kill -0 "$PID" 2>/dev/null && kill -9 "$PID" || true
  echo "已停止拾忆应用（PID $PID）"
else
  echo "PID $PID 已不存在"
fi
rm -f "$PIDFILE"
