# vid2text 单文件安装（Windows PowerShell）
#   irm https://raw.githubusercontent.com/cnwinds/vid2text/master/deploy/get-vid2text.ps1 | iex
# 国内镜像示例:
#   irm https://ghfast.top/https://raw.githubusercontent.com/cnwinds/vid2text/master/deploy/get-vid2text.ps1 | iex
param(
  [string]$Dir = ""
)

$ErrorActionPreference = "Stop"
$RepoRaw = if ($env:VID2TEXT_REPO_RAW) { $env:VID2TEXT_REPO_RAW } else { "https://raw.githubusercontent.com/cnwinds/vid2text/master" }
$Dest = if ($Dir) { $Dir } elseif ($env:VID2TEXT_DIR) { $env:VID2TEXT_DIR } else { Join-Path (Get-Location) "vid2text" }

function Save-Utf8Bom([string]$Path, [string]$Content) {
  $utf8Bom = New-Object System.Text.UTF8Encoding $true
  [System.IO.File]::WriteAllText($Path, $Content, $utf8Bom)
}

Write-Host "[vid2text] Install dir: $Dest"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
$launcher = Join-Path $Dest "vid2text.ps1"
Write-Host "[vid2text] Downloading vid2text.ps1 (single-file launcher)"
# Download as text and rewrite with UTF-8 BOM so Windows PowerShell 5.1
# does not mis-decode the script as system ANSI (GBK) on Chinese Windows.
$launcherText = (Invoke-WebRequest -Uri "$RepoRaw/deploy/vid2text.ps1" -UseBasicParsing).Content
Save-Utf8Bom $launcher $launcherText

Set-Location $Dest
Write-Host "[vid2text] Ready. Only vid2text.ps1 is required in this folder."
& $launcher start
