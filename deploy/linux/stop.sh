#!/usr/bin/env bash
# PlugHub 插件库：停止 start.sh 拉起的后台进程。
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
PID_FILE="$ROOT_DIR/data/runtime/pluginhub.pid"

if [ ! -f "$PID_FILE" ]; then
  printf '[PlugHub] 未发现 PID 文件，可能没有在运行的实例\n'
  exit 0
fi

PID=$(cat "$PID_FILE")
if ! kill -0 "$PID" 2>/dev/null; then
  printf '[PlugHub] 进程 %s 已不存在，清理 PID 文件\n' "$PID"
  rm -f "$PID_FILE"
  exit 0
fi

kill "$PID"
for _ in $(seq 1 20); do
  if ! kill -0 "$PID" 2>/dev/null; then
    rm -f "$PID_FILE"
    printf '[PlugHub] 已停止（PID %s）\n' "$PID"
    exit 0
  fi
  sleep 0.5
done

kill -9 "$PID" 2>/dev/null || true
rm -f "$PID_FILE"
printf '[PlugHub] 进程未响应，已强制结束（PID %s）\n' "$PID"
