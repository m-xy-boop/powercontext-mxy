#!/usr/bin/env bash
# 启动真实 PowerContext Server（SQLite 持久化，仅监听回环地址）。
# 数据目录固定在本项目 .powercontext/，记忆跨重启存在，不写入其他赛题目录。
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${POWERCONTEXT_HOST:-127.0.0.1}"
PORT="${POWERCONTEXT_PORT:-8777}"
export POWERCONTEXT_HOME="$PWD/.powercontext"
export POWERCONTEXT_SERVER_HTTP_PORT="$PORT"
mkdir -p "$POWERCONTEXT_HOME" var/log var/run

CLI="${POWERCONTEXT_CLI:-}"
if [ -z "$CLI" ]; then
  for candidate in /home/xy/.local/bin/powercontext "$(command -v powercontext || true)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then CLI="$candidate"; break; fi
  done
fi
if [ -z "$CLI" ]; then
  echo "未找到 powercontext 命令。请先安装：uv tool install 'powercontext[cli,server]==1.5.1' --index-url https://pypi.tuna.tsinghua.edu.cn/simple" >&2
  exit 1
fi

if curl -sf -m 2 "http://$HOST:$PORT/health/ready" >/dev/null 2>&1; then
  echo "PowerContext 已在 http://$HOST:$PORT 就绪（复用现有进程，数据目录 $POWERCONTEXT_HOME）"
  exit 0
fi

if [ -f var/run/powercontext.pid ] && kill -0 "$(cat var/run/powercontext.pid)" 2>/dev/null; then
  echo "已有由本脚本启动的进程 $(cat var/run/powercontext.pid)，等待其就绪" >&2
else
  nohup "$CLI" server run --host "$HOST" --port "$PORT" >>var/log/powercontext-server.log 2>&1 &
  echo $! >var/run/powercontext.pid
  echo "已启动 PowerContext Server，PID $(cat var/run/powercontext.pid)，日志 var/log/powercontext-server.log"
fi

for _ in $(seq 1 60); do
  if curl -sf -m 2 "http://$HOST:$PORT/health/ready" >/dev/null 2>&1; then
    echo "就绪：http://$HOST:$PORT（$("$CLI" --version 2>/dev/null | head -1)）"
    exit 0
  fi
  sleep 0.5
done

echo "PowerContext 在 30 秒内未就绪，请查看 var/log/powercontext-server.log" >&2
exit 1
