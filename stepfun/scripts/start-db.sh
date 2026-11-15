#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
instance_dir="${SEEKDB_BASE_DIR:-$PWD/.runtime/seekdb}"
port="${SEEKDB_PORT:-2883}"
if ! command -v seekdb >/dev/null; then
  echo '请安装官方 seekdb >= 1.4.0：https://github.com/oceanbase/seekdb/releases' >&2
  exit 1
fi
mkdir -p "$instance_dir"
seekdb_args=(--base-dir="$instance_dir" --port="$port" --parameter cpu_count=2
  --parameter memory_budget=2G --parameter datafile_size=512M
  --parameter datafile_maxsize=4G --parameter log_disk_size=4G)
if [[ -f /run/systemd/container && "$(cat /run/systemd/container)" == 'wsl' ]]; then
  # Official 1.4 telemetry misidentifies this WSL marker as a container.
  # Hide it in the child mount namespace; the host filesystem is unchanged.
  unshare --user --map-root-user --mount --fork sh -c '
    mount -t tmpfs tmpfs /run/systemd
    exec seekdb "$@"
  ' sh "${seekdb_args[@]}"
else
  seekdb "${seekdb_args[@]}"
fi
echo "seekdb 启动命令已发送，应用连接 127.0.0.1:$port；首次初始化请等待就绪。"
