#!/usr/bin/env bash
# 停止由 scripts/start_powercontext.sh 启动的 PowerContext Server。
# 只终止本脚本记录的 PID：持久化数据保留在 .powercontext/，重启后记忆仍在。
set -euo pipefail
cd "$(dirname "$0")/.."

PIDFILE=var/run/powercontext.pid
if [ ! -f "$PIDFILE" ]; then
  echo "没有 var/run/powercontext.pid：本脚本没有启动过服务，不做任何终止操作。" >&2
  echo "如需手动停止，请确认进程归属后执行 pkill -f 'powercontext server run'" >&2
  exit 1
fi

PID="$(cat "$PIDFILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  for _ in $(seq 1 40); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.25
  done
  if kill -0 "$PID" 2>/dev/null; then
    echo "PID $PID 未在 10 秒内退出，发送 SIGKILL" >&2
    kill -9 "$PID"
  fi
  echo "已停止 PowerContext Server（PID $PID）"
else
  echo "PID $PID 已不存在，清理 pid 文件"
fi
rm -f "$PIDFILE"
