#!/usr/bin/env bash
# 下载单文件启动器并启动。
# Linux/macOS:
#   curl -fsSL https://raw.githubusercontent.com/cnwinds/vid2text/master/deploy/get-vid2text.sh | bash
# 国内镜像示例:
#   curl -fsSL https://ghfast.top/https://raw.githubusercontent.com/cnwinds/vid2text/master/deploy/get-vid2text.sh | bash
# Windows（PowerShell）请改用:
#   irm https://raw.githubusercontent.com/cnwinds/vid2text/master/deploy/get-vid2text.ps1 | iex
set -euo pipefail

REPO_RAW="${VID2TEXT_REPO_RAW:-https://raw.githubusercontent.com/cnwinds/vid2text/master}"
DEST="${VID2TEXT_DIR:-${PWD}/vid2text}"

echo "[vid2text] 安装目录: ${DEST}"
mkdir -p "${DEST}"
echo "[vid2text] 下载 vid2text.sh（单文件启动器）"
curl -fsSL "${REPO_RAW}/deploy/vid2text.sh" -o "${DEST}/vid2text.sh"
chmod +x "${DEST}/vid2text.sh"
cd "${DEST}"

echo "[vid2text] 就绪。仅需本目录下的 vid2text.sh 即可启动。"
exec ./vid2text.sh start
