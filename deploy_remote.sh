#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME='napcatqq-service'
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker-compose.remote.yml"
ENV_FILE="$ROOT_DIR/.env.remote"
cd "$ROOT_DIR"

red='\033[0;31m'; green='\033[0;32m'; yellow='\033[1;33m'; cyan='\033[0;36m'; reset='\033[0m'
info() { echo -e "${cyan}[信息] $*${reset}"; }
success() { echo -e "${green}[成功] $*${reset}"; }
warn() { echo -e "${yellow}[警告] $*${reset}"; }
fail() { echo -e "${red}[错误] $*${reset}" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || fail '未找到 Docker CLI。'
docker compose version >/dev/null 2>&1 || fail '未找到 Docker Compose v2。'

create_env_file() {
  umask 077
  if [ -f "$ROOT_DIR/.env.remote.example" ]; then
    cp "$ROOT_DIR/.env.remote.example" "$ENV_FILE"
  else
    cat > "$ENV_FILE" <<'EOF'
# 远程镜像部署配置，请填写 CHANGE_ME 和地址占位符后重新执行脚本。
IMAGE_REGISTRY=registry.cn-shanghai.aliyuncs.com
IMAGE_NAMESPACE=zhinian-software
IMAGE_TAG=latest

BACKEND_PORT=8000
FRONTEND_PORT=5178

DATABASE_URL=mysql+aiomysql://napcat:CHANGE_ME@数据库地址:3306/napcat?charset=utf8mb4
DATABASE_CONNECT_TIMEOUT=10
ADMIN_USERNAME=admin
ADMIN_PASSWORD=CHANGE_ME_ADMIN_PASSWORD

# 应用加密密钥：用于加密保存 SMTP 密码、AI 平台 API Key，并参与邮箱验证码哈希。
# 请替换为随机长字符串；服务运行后不要随意修改，否则历史加密配置无法解密。
SETTINGS_ENCRYPTION_SECRET=CHANGE_ME_SETTINGS_ENCRYPTION_SECRET

# NapCat HTTP Client/反向 WebSocket 使用的 Token（必填，请替换占位符）
NAPCAT_TOKEN=CHANGE_ME_NAPCAT_TOKEN
# 业务侧动作代理和 /ws 使用的 Token（必填，请替换占位符）
SERVICE_TOKEN=CHANGE_ME_SERVICE_TOKEN
PUBLIC_SERVICE_URL=
REDIS_URL=redis://:CHANGE_ME@Redis地址:6379/0
AI_FOLLOWUP_TTL=259200
CORS_ORIGINS=*
TZ=Asia/Shanghai
LOG_LEVEL=INFO
LOG_FILE=./data/logs/napcat_service.log
LOG_BACKUP_COUNT=30
HOST=0.0.0.0
PORT=8000
EOF
  fi
  chmod 600 "$ENV_FILE"
}

create_compose_file() {
  if [ -f "$COMPOSE_FILE" ]; then
    return
  fi
  cat > "$COMPOSE_FILE" <<'EOF'
name: napcatqq-service

services:
  backend:
    image: ${IMAGE_REGISTRY:?请配置 IMAGE_REGISTRY}/${IMAGE_NAMESPACE:?请配置 IMAGE_NAMESPACE}/napcatqq-service-backend:${IMAGE_TAG:-latest}
    container_name: napcatqq-service-backend
    env_file:
      - ./.env.remote
    environment:
      HOST: 0.0.0.0
      PORT: 8000
      TZ: ${TZ:-Asia/Shanghai}
    volumes:
      - ./data:/app/data
    ports:
      - "${BACKEND_PORT:-8000}:8000"
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import json,urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)); raise SystemExit(0 if data.get('success') else 1)\""]
      interval: 20s
      timeout: 10s
      retries: 5
      start_period: 40s
    extra_hosts:
      - "host.docker.internal:host-gateway"
    networks:
      - napcatqq-service-network

  frontend:
    image: ${IMAGE_REGISTRY:?请配置 IMAGE_REGISTRY}/${IMAGE_NAMESPACE:?请配置 IMAGE_NAMESPACE}/napcatqq-service-frontend:${IMAGE_TAG:-latest}
    container_name: napcatqq-service-frontend
    depends_on:
      backend:
        condition: service_healthy
    ports:
      - "${FRONTEND_PORT:-5178}:80"
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1/health | grep -q '\"success\":true'"]
      interval: 20s
      timeout: 10s
      retries: 3
      start_period: 10s
    networks:
      - napcatqq-service-network

networks:
  napcatqq-service-network:
    name: napcatqq-service-network
EOF
  chmod 644 "$COMPOSE_FILE"
  info '已生成 docker-compose.remote.yml'
}

create_compose_file

if [ ! -f "$ENV_FILE" ]; then
  create_env_file
  fail '已生成 .env.remote，请填写镜像仓库、数据库、Redis 和管理员配置后重新执行。'
fi

env_value() {
  key="$1"
  value=$(sed -n "s/^${key}=//p" "$ENV_FILE" | tail -n 1 | tr -d '\r')
  value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
  printf '%s' "$value"
}

for key in IMAGE_REGISTRY IMAGE_NAMESPACE DATABASE_URL ADMIN_PASSWORD SETTINGS_ENCRYPTION_SECRET; do
  value=$(env_value "$key")
  case "$value" in
    ''|*CHANGE_ME*|*数据库地址*|*Redis地址*) fail ".env.remote 缺少必要配置：$key" ;;
  esac
done

database_url=$(env_value DATABASE_URL)
redis_url=$(env_value REDIS_URL)
case "$database_url" in
  *@localhost:*|*@127.0.0.1:*) warn 'DATABASE_URL 使用 localhost/127.0.0.1，容器内通常应改为数据库服务器地址。' ;;
esac
case "$redis_url" in
  *@localhost:*|*@127.0.0.1:*) warn 'REDIS_URL 使用 localhost/127.0.0.1，容器内通常应改为 Redis 服务器地址。' ;;
esac

docker info >/dev/null || fail 'Docker 服务不可用。'
mkdir -p "$ROOT_DIR/data"
compose() { docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" -p "$PROJECT_NAME" "$@"; }
compose config --quiet

image_registry=$(env_value IMAGE_REGISTRY)
image_namespace=$(env_value IMAGE_NAMESPACE)
image_tag=$(env_value IMAGE_TAG); image_tag=${image_tag:-latest}
backend_image="$image_registry/$image_namespace/napcatqq-service-backend:$image_tag"
frontend_image="$image_registry/$image_namespace/napcatqq-service-frontend:$image_tag"
previous_backend=$(docker image inspect --format '{{.Id}}' "$backend_image" 2>/dev/null || true)
previous_frontend=$(docker image inspect --format '{{.Id}}' "$frontend_image" 2>/dev/null || true)

info '拉取远程镜像...'
compose pull
info '使用新镜像更新服务...'
if ! compose up -d --remove-orphans; then
  compose ps || true
  compose logs --no-color --tail 200 backend frontend || true
  exit 1
fi

wait_for_health() {
  container="$1"; timeout_seconds="$2"; start_time=$(date +%s)
  while :; do
    status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)
    case "$status" in
      healthy) success "$container 健康检查通过"; return 0 ;;
      exited|dead) compose ps || true; compose logs --no-color --tail 200 backend frontend || true; fail "$container 启动失败，状态：$status" ;;
    esac
    if [ $(( $(date +%s) - start_time )) -ge "$timeout_seconds" ]; then
      compose ps || true; compose logs --no-color --tail 200 backend frontend || true
      fail "$container 在 ${timeout_seconds}s 内未通过健康检查"
    fi
    sleep 3
  done
}

wait_for_health napcatqq-service-backend 180
wait_for_health napcatqq-service-frontend 120
current_backend=$(docker image inspect --format '{{.Id}}' "$backend_image" 2>/dev/null || true)
current_frontend=$(docker image inspect --format '{{.Id}}' "$frontend_image" 2>/dev/null || true)
[ -n "$previous_backend" ] && [ "$previous_backend" != "$current_backend" ] && docker image rm "$previous_backend" >/dev/null 2>&1 || true
[ -n "$previous_frontend" ] && [ "$previous_frontend" != "$current_frontend" ] && docker image rm "$previous_frontend" >/dev/null 2>&1 || true
compose ps
frontend_port=$(env_value FRONTEND_PORT); frontend_port=${frontend_port:-5178}
success 'NapCatQQ Service 远程部署完成'
echo "前端入口：http://服务器IP:$frontend_port"
echo "健康检查：http://服务器IP:$frontend_port/health"
