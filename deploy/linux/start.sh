#!/usr/bin/env bash
# PlugHub 插件库：Linux 裸机一键启动（不使用 Docker）。
#
# 做四件事：准备虚拟环境、安装后端依赖、构建前端、后台拉起后端。
# 后端会同时托管前端产物（FRONTEND_DIST），因此只需暴露一个端口。
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$ROOT_DIR"

VENV_DIR="$ROOT_DIR/.venv"
PID_FILE="$ROOT_DIR/data/runtime/pluginhub.pid"
CONSOLE_LOG="$ROOT_DIR/data/logs/pluginhub-console.log"
PORT="${PORT:-8120}"

log() { printf '[PlugHub] %s\n' "$*"; }
fail() { printf '[PlugHub][错误] %s\n' "$*" >&2; exit 1; }

# ---------- 环境检查 ----------
command -v python3 >/dev/null 2>&1 || fail "未找到 python3，请先安装 Python 3.11 或更高版本"
command -v npm >/dev/null 2>&1 || fail "未找到 npm，请先安装 Node.js 18 或更高版本"
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
  || fail "Python 版本过低，需要 3.11 及以上"

# ---------- 已在运行则先停 ----------
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  log "检测到正在运行的实例，先停止"
  "$ROOT_DIR/deploy/linux/stop.sh"
fi

# ---------- 配置 ----------
if [ ! -f "$ROOT_DIR/.env" ]; then
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  chmod 600 "$ROOT_DIR/.env"
  fail "已生成 .env，请填写 ADMIN_PASSWORD 与 SETTINGS_ENCRYPTION_SECRET 后重新执行"
fi

# ---------- 后端依赖 ----------
if [ ! -d "$VENV_DIR" ]; then
  log "创建虚拟环境 .venv"
  python3 -m venv "$VENV_DIR"
fi
log "安装后端依赖"
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install --editable "$ROOT_DIR/backend" --quiet

# ---------- 前端构建 ----------
log "构建前端（首次会比较慢）"
(
  cd "$ROOT_DIR/frontend"
  if [ -f package-lock.json ]; then
    npm ci --no-audit --no-fund
  else
    npm install --no-audit --no-fund
  fi
  npm run build
)

# ---------- 启动后端 ----------
mkdir -p "$(dirname "$PID_FILE")" "$(dirname "$CONSOLE_LOG")"
export FRONTEND_DIST="$ROOT_DIR/frontend/dist"
export PORT
(
  cd "$ROOT_DIR/backend"
  nohup "$VENV_DIR/bin/python" main.py >> "$CONSOLE_LOG" 2>&1 &
  echo $! > "$PID_FILE"
)
log "已启动，PID=$(cat "$PID_FILE")，日志：$CONSOLE_LOG"

# ---------- 健康检查 ----------
for _ in $(seq 1 60); do
  if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    log "启动成功：http://<服务器IP>:$PORT"
    exit 0
  fi
  sleep 1
done
fail "启动超时，请查看日志：$CONSOLE_LOG"
