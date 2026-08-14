#!/usr/bin/env bash
# vid2text 单文件启动器（Linux / macOS）
# 用法: ./vid2text.sh start
# 自包含：运行时在脚本旁写出 compose / .env，无需仓库其它文件。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_IMAGE="ghcr.io/cnwinds/vid2text:latest"

usage() {
  cat <<'EOF'
Usage: ./vid2text.sh <command>

Commands:
  start     拉取镜像并启动
  stop      停止
  update    拉取最新镜像并启动
  prepare   仅写出 compose / .env（不启动）
  log|logs  查看日志
  help      帮助
EOF
}

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "[vid2text] 缺少命令: $1（请先安装 Docker / Docker Desktop）" >&2
    exit 1
  fi
}

write_file() {
  local path="$1"
  mkdir -p "$(dirname "${path}")"
  cat >"${path}"
}

rand_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 12
  else
    head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

materialize_bundle() {
  mkdir -p "${ROOT}/data"
  write_file "${ROOT}/docker-compose.yml" <<'VID2TEXT_EOF'
# 由 deploy/vid2text.sh 写出（生产预构建镜像）
services:
  vid2text:
    image: ${VID2TEXT_IMAGE:-ghcr.io/cnwinds/vid2text:latest}
    container_name: vid2text
    restart: unless-stopped
    ports:
      - "${VID2TEXT_PORT:-8000}:8000"
    volumes:
      - ./data:/app/data
    env_file:
      - .env
    environment:
      - SENSEVOICE_OFFLINE=1
      - SENSEVOICE_MODEL_DIR=/app/models/SenseVoiceSmall-onnx
      - WORK_CACHE_QUOTA_GB=${WORK_CACHE_QUOTA_GB:-2}
      - HTTP_PROXY=${HTTP_PROXY:-}
      - HTTPS_PROXY=${HTTPS_PROXY:-}
      - NO_PROXY=${NO_PROXY:-localhost,127.0.0.1,host.docker.internal,douyin.com,iesdouyin.com,bilibili.com,b23.tv,modelscope.cn,aliyuncs.com}
      - http_proxy=${HTTP_PROXY:-}
      - https_proxy=${HTTPS_PROXY:-}
      - no_proxy=${NO_PROXY:-localhost,127.0.0.1,host.docker.internal,douyin.com,iesdouyin.com,bilibili.com,b23.tv,modelscope.cn,aliyuncs.com}
    extra_hosts:
      - "host.docker.internal:host-gateway"
    shm_size: "2gb"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
VID2TEXT_EOF

  write_file "${ROOT}/.env.example" <<'VID2TEXT_EOF'
# 由单文件启动器写出。首次 start 会自动生成 ADMIN_*（也可自行修改）。

TZ=Asia/Shanghai
VID2TEXT_PORT=8000

# 管理鉴权（必填其一；启动器会自动生成）
ADMIN_PASSWORD=change-me
ADMIN_API_TOKEN=change-me-admin-token

# （可选）字幕公开 API 鉴权
# PUBLIC_API_TOKEN=

# LLM 后处理（OpenAI 兼容；不配则跳过修正）
# LLM_PROVIDER=openai
# OPENAI_API_KEY=
# OPENAI_BASE_URL=https://api.openai.com/v1
# OPENAI_MODEL=gpt-4o-mini

WORK_CACHE_QUOTA_GB=2
STEP_CONCURRENCY_JSON={"download":1,"stt":1,"correct":1,"default":1}

# 访问 YouTube 等海外站点时可设代理
# HTTP_PROXY=http://host.docker.internal:7890
# HTTPS_PROXY=http://host.docker.internal:7890

YTDLP_YOUTUBE_MIN_INTERVAL_SEC=6
LOG_LEVEL=INFO
LOG_FORMAT=text

# VID2TEXT_IMAGE=ghcr.io/cnwinds/vid2text:latest
VID2TEXT_EOF

  ensure_env
}

ensure_env() {
  if [[ -f "${ROOT}/.env" ]]; then
    return
  fi
  local pw token
  pw="$(rand_secret)"
  token="v2t_$(rand_secret)"
  cp "${ROOT}/.env.example" "${ROOT}/.env"
  # portable in-place replace without relying on GNU sed -i
  local tmp
  tmp="$(mktemp)"
  sed \
    -e "s/^ADMIN_PASSWORD=.*/ADMIN_PASSWORD=${pw}/" \
    -e "s/^ADMIN_API_TOKEN=.*/ADMIN_API_TOKEN=${token}/" \
    "${ROOT}/.env" >"${tmp}"
  mv "${tmp}" "${ROOT}/.env"
  echo "[vid2text] 已创建 .env，并生成管理凭据："
  echo "           ADMIN_PASSWORD=${pw}"
  echo "           ADMIN_API_TOKEN=${token}"
  echo "           （请妥善保存；之后可编辑 ${ROOT}/.env）"
}

run_compose() {
  need_cmd docker
  if ! docker compose version >/dev/null 2>&1; then
    echo "[vid2text] 需要 Docker Compose 插件（docker compose）" >&2
    exit 1
  fi
  docker compose --project-directory "${ROOT}" --env-file "${ROOT}/.env" \
    -f "${ROOT}/docker-compose.yml" "$@"
}

web_port() {
  local port
  port="$(grep -E '^VID2TEXT_PORT=' "${ROOT}/.env" 2>/dev/null | cut -d= -f2- | tr -d '\r' || true)"
  echo "${port:-8000}"
}

do_start() {
  materialize_bundle
  echo "[vid2text] 正在拉取镜像并启动（默认 ${DEFAULT_IMAGE}）..."
  echo "[vid2text] 首次镜像较大，请耐心等待。"
  run_compose pull
  run_compose up -d
  echo "[vid2text] 就绪 → http://localhost:$(web_port)"
  echo "[vid2text] 日志 → ./vid2text.sh log"
  echo "[vid2text] 管理页 /monitors、/settings 使用 .env 中的 ADMIN_PASSWORD 登录。"
}

do_stop() {
  materialize_bundle
  run_compose down --remove-orphans
  echo "[vid2text] 已停止"
}

do_update() {
  do_start
}

do_log() {
  materialize_bundle
  run_compose logs -f --tail=50
}

do_prepare() {
  materialize_bundle
  echo "[vid2text] 已写出 docker-compose.yml / .env.example / .env"
  echo "[vid2text] 目录: ${ROOT}"
}

cmd="${1:-help}"
shift || true
case "${cmd}" in
  start) do_start ;;
  stop) do_stop ;;
  update) do_update ;;
  prepare) do_prepare ;;
  log|logs) do_log ;;
  help|-h|--help) usage ;;
  *)
    usage >&2
    exit 1
    ;;
esac
