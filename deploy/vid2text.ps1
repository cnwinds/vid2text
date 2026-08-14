# vid2text 单文件启动器（Windows PowerShell）
# 用法: .\vid2text.ps1 start
# 自包含：运行时在脚本旁写出 compose / .env，无需仓库其它文件。
param(
  [Parameter(Position = 0)]
  [ValidateSet("start", "stop", "update", "prepare", "log", "logs", "help")]
  [string]$Command = "help"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Root) { $Root = Get-Location }
$DefaultImage = "ghcr.io/cnwinds/vid2text:latest"

function Show-Usage {
  @"
Usage: .\vid2text.ps1 <command>

Commands:
  start     拉取镜像并启动
  stop      停止
  update    拉取最新镜像并启动
  prepare   仅写出 compose / .env（不启动）
  log|logs  查看日志
  help      帮助
"@
}

function Need-Cmd([string]$Name) {
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    Write-Error "[vid2text] 缺少命令: $Name（请先安装 Docker / Docker Desktop）"
  }
}

function Save-Utf8NoBom([string]$Path, [string]$Content) {
  $utf8 = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($Path, $Content, $utf8)
}

function New-Secret {
  $bytes = New-Object byte[] 12
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

function Materialize-Bundle {
  New-Item -ItemType Directory -Force -Path (Join-Path $Root "data") | Out-Null
  $compose = @'
# 由 deploy/vid2text.ps1 写出（生产预构建镜像）
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
'@
  Save-Utf8NoBom (Join-Path $Root "docker-compose.yml") $compose

  $envExample = @'
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
'@
  Save-Utf8NoBom (Join-Path $Root ".env.example") $envExample
  Ensure-Env
}

function Ensure-Env {
  $envPath = Join-Path $Root ".env"
  if (Test-Path $envPath) { return }
  $pw = New-Secret
  $token = "v2t_$(New-Secret)"
  $text = Get-Content (Join-Path $Root ".env.example") -Raw
  $text = $text -replace '(?m)^ADMIN_PASSWORD=.*$', "ADMIN_PASSWORD=$pw"
  $text = $text -replace '(?m)^ADMIN_API_TOKEN=.*$', "ADMIN_API_TOKEN=$token"
  Save-Utf8NoBom $envPath $text
  Write-Host "[vid2text] 已创建 .env，并生成管理凭据："
  Write-Host "           ADMIN_PASSWORD=$pw"
  Write-Host "           ADMIN_API_TOKEN=$token"
  Write-Host "           （请妥善保存；之后可编辑 $envPath）"
}

function Invoke-Compose {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$ComposeArgs)
  Need-Cmd docker
  $null = docker compose version
  if ($LASTEXITCODE -ne 0) {
    Write-Error "[vid2text] 需要 Docker Compose 插件（docker compose）"
  }
  $envFile = Join-Path $Root ".env"
  $composeFile = Join-Path $Root "docker-compose.yml"
  & docker compose --project-directory $Root --env-file $envFile -f $composeFile @ComposeArgs
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Get-WebPort {
  $envPath = Join-Path $Root ".env"
  if (Test-Path $envPath) {
    $line = Get-Content $envPath | Where-Object { $_ -match '^VID2TEXT_PORT=' } | Select-Object -First 1
    if ($line) {
      return ($line -replace '^VID2TEXT_PORT=', '').Trim()
    }
  }
  return "8000"
}

function Do-Start {
  Materialize-Bundle
  Write-Host "[vid2text] 正在拉取镜像并启动（默认 $DefaultImage）..."
  Write-Host "[vid2text] 首次镜像较大，请耐心等待。"
  Invoke-Compose pull
  Invoke-Compose up -d
  $port = Get-WebPort
  Write-Host "[vid2text] 就绪 → http://localhost:$port"
  Write-Host "[vid2text] 日志 → .\vid2text.ps1 log"
  Write-Host "[vid2text] 管理页 /monitors、/settings 使用 .env 中的 ADMIN_PASSWORD 登录。"
}

function Do-Stop {
  Materialize-Bundle
  Invoke-Compose down --remove-orphans
  Write-Host "[vid2text] 已停止"
}

function Do-Log {
  Materialize-Bundle
  Invoke-Compose logs -f --tail=50
}

function Do-Prepare {
  Materialize-Bundle
  Write-Host "[vid2text] 已写出 docker-compose.yml / .env.example / .env"
  Write-Host "[vid2text] 目录: $Root"
}

switch ($Command) {
  "start" { Do-Start }
  "stop" { Do-Stop }
  "update" { Do-Start }
  "prepare" { Do-Prepare }
  "log" { Do-Log }
  "logs" { Do-Log }
  default { Show-Usage }
}
