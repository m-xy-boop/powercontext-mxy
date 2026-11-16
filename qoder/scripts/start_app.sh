#!/usr/bin/env bash
# 启动拾忆应用（FastAPI + 静态页面）。默认端口 8642，只监听回环地址。
# 应用不需要任何模型 API 密钥：判分与选题在本地服务端完成，记忆读写走 PowerContext 公开接口。
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${APP_HOST:-127.0.0.1}"
PORT="${APP_PORT:-8642}"
export POWERCONTEXT_URL="${POWERCONTEXT_URL:-http://127.0.0.1:${POWERCONTEXT_PORT:-8777}}"
UV="${UV:-$HOME/.local/bin/uv}"
command -v "$UV" >/dev/null 2>&1 || UV="$(command -v uv)"
mkdir -p var/log var/run data/runtime

if curl -sf -m 2 "http://$HOST:$PORT/api/context" >/dev/null 2>&1; then
  echo "拾忆应用已在 http://$HOST:$PORT 运行（复用现有进程）"
  exit 0
fi

if ! curl -sf -m 2 "$POWERCONTEXT_URL/health/ready" >/dev/null 2>&1; then
  echo "先启动 PowerContext：scripts/start_powercontext.sh（应用不会用模拟记忆顶替）" >&2
  exit 1
fi

nohup "$UV" run uvicorn app.main:app --host "$HOST" --port "$PORT" --log-level info >>var/log/app.log 2>&1 &
echo $! >var/run/app.pid
sleep 1

for _ in $(seq 1 40); do
  if curl -sf -m 2 "http://$HOST:$PORT/api/context" >/dev/null 2>&1; then
    echo "拾忆 · Recall Coach 已就绪：http://$HOST:$PORT（PID $(cat var/run/app.pid)，后端 $POWERCONTEXT_URL）"
    echo "页面：/ 概览 · /diagnose 诊断与复习 · /memory 学习记忆 · /verify 验证说明"
    exit 0
  fi
  sleep 0.5
done

echo "应用未能在 20 秒内启动，请查看 var/log/app.log" >&2
exit 1
