#!/usr/bin/env sh
set -eu

PROJECT_NAME='napcatqq-service'
ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COMPOSE_FILE="$ROOT_DIR/docker-compose.yml"
ENV_FILE="$ROOT_DIR/.env"
cd "$ROOT_DIR"

red='\033[0;31m'; green='\033[0;32m'; yellow='\033[1;33m'; cyan='\033[0;36m'; reset='\033[0m'
info() { printf '%b\n' "${cyan}[信息] $*${reset}"; }
success() { printf '%b\n' "${green}[成功] $*${reset}"; }
warn() { printf '%b\n' "${yellow}[警告] $*${reset}"; }
fail() { printf '%b\n' "${red}[错误] $*${reset}" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || fail '未找到 Docker CLI，请先安装并启动 Docker。'
docker compose version >/dev/null 2>&1 || fail '未找到 Docker Compose v2。'
if [ ! -f "$ENV_FILE" ]; then
  cp "$ROOT_DIR/.env.example" "$ENV_FILE"
  fail '首次部署已生成 .env，请填写数据库、管理员密码、Redis 和 NapCat 配置后重新执行。'
fi

env_value() {
  key="$1"
  value=$(sed -n "s/^${key}=//p" "$ENV_FILE" | tail -n 1 | tr -d '\r')
  value=${value#\"}; value=${value%\"}; value=${value#\'}; value=${value%\'}
  printf '%s' "$value"
}

for key in DATABASE_URL ADMIN_PASSWORD SETTINGS_ENCRYPTION_SECRET; do
  value=$(env_value "$key")
  case "$value" in ''|*CHANGE_ME*) fail ".env 缺少必要配置：$key" ;; esac
done
database_url=$(env_value DATABASE_URL)
redis_url=$(env_value REDIS_URL)
case "$database_url" in *@localhost:*|*@127.0.0.1:*) warn 'DATABASE_URL 使用 localhost/127.0.0.1，容器内通常应改为 host.docker.internal 或数据库服务器地址。' ;; esac
case "$redis_url" in *@localhost:*|*@127.0.0.1:*) warn 'REDIS_URL 使用 localhost/127.0.0.1，容器内通常应改为 host.docker.internal 或 Redis 服务器地址。' ;; esac

mkdir -p "$ROOT_DIR/data"

image_ref() {
  service="$1"
  case "$service" in
    backend) image_name=$(env_value BACKEND_IMAGE); image_name=${image_name:-napcatqq-service-backend} ;;
    frontend) image_name=$(env_value FRONTEND_IMAGE); image_name=${image_name:-napcatqq-service-frontend} ;;
    *) fail "未知服务：$service" ;;
  esac
  image_tag=$(env_value IMAGE_TAG); image_tag=${image_tag:-latest}
  printf '%s:%s' "$image_name" "$image_tag"
}

project_images="$(image_ref backend)
$(image_ref frontend)"

docker info >/dev/null || fail 'Docker 服务不可用，请先启动 Docker。'
compose() { docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" -p "$PROJECT_NAME" "$@"; }
compose config --quiet
info '停止本项目已有容器...'
compose down --remove-orphans

while IFS= read -r image; do
  [ -n "$image" ] || continue
  if docker image inspect "$image" >/dev/null 2>&1; then
    info "删除本项目旧镜像：$image"
    if ! docker image rm "$image"; then
      fail "旧镜像无法删除，可能仍被其他容器使用：$image"
    fi
  fi
done <<EOF
$project_images
EOF

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

if ! compose up -d --build --remove-orphans; then
  compose ps || true
  compose logs --no-color --tail 200 backend frontend || true
  exit 1
fi
wait_for_health napcatqq-service-backend 180
wait_for_health napcatqq-service-frontend 120
compose ps

frontend_port=$(env_value FRONTEND_PORT); frontend_port=${frontend_port:-5178}
printf '%b\n' "${green}==========================================${reset}"
success 'NapCatQQ Service 部署完成'
printf '前端入口：http://服务器IP:%s\n' "$frontend_port"
printf '健康检查：http://服务器IP:%s/health\n' "$frontend_port"
