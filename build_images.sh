#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT_DIR"
REGISTRY=${IMAGE_REGISTRY:-}
NAMESPACE=${IMAGE_NAMESPACE:-}
TAG=${IMAGE_TAG:-latest}
PLATFORM=${BUILD_PLATFORM:-linux/amd64}

command -v docker >/dev/null 2>&1 || { echo '未找到 Docker CLI。' >&2; exit 1; }
docker info >/dev/null
if [ "${PUSH_IMAGES:-0}" = '1' ] && [ -z "$REGISTRY" ]; then
  echo '推送镜像时必须设置 IMAGE_REGISTRY。' >&2
  exit 1
fi
if [ -n "$NAMESPACE" ] && [ -z "$REGISTRY" ]; then
  echo '设置 IMAGE_NAMESPACE 时必须同时设置 IMAGE_REGISTRY。' >&2
  exit 1
fi
if [ -z "$TAG" ]; then
  echo 'IMAGE_TAG 不能为空。' >&2
  exit 1
fi

prefix="$REGISTRY"
[ -n "$NAMESPACE" ] && prefix="$prefix/$NAMESPACE"
if [ -n "$prefix" ]; then
  backend_image="$prefix/napcatqq-service-backend:$TAG"
  frontend_image="$prefix/napcatqq-service-frontend:$TAG"
else
  backend_image="napcatqq-service-backend:$TAG"
  frontend_image="napcatqq-service-frontend:$TAG"
fi

docker build --provenance=false --platform "$PLATFORM" -t "$backend_image" -f backend/Dockerfile .
docker build --provenance=false --platform "$PLATFORM" -t "$frontend_image" -f frontend/Dockerfile .
if [ "${PUSH_IMAGES:-0}" = '1' ]; then
  docker push "$backend_image"
  docker push "$frontend_image"
fi
echo "镜像构建完成：$backend_image、$frontend_image"
