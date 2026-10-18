param(
    [string]$Registry = '',
    [string]$Namespace = '',
    [string]$Tag = 'latest',
    [string]$Platform = 'linux/amd64',
    [switch]$Push
)

$ErrorActionPreference = 'Stop'
chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

function Invoke-DockerCommand {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker 命令执行失败: docker $($Arguments -join ' ')" }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw '未找到 Docker CLI。' }
Invoke-DockerCommand -Arguments @('info')
if ($Push -and [string]::IsNullOrWhiteSpace($Registry)) { throw '推送镜像时必须提供 -Registry。' }
if ($Namespace -and [string]::IsNullOrWhiteSpace($Registry)) { throw '提供 -Namespace 时必须同时提供 -Registry。' }
if ([string]::IsNullOrWhiteSpace($Tag)) { throw '-Tag 不能为空。' }

$imagePrefix = if ($Registry) {
    if ($Namespace) { "$Registry/$Namespace" } else { $Registry }
} else { '' }
$backendImage = if ($imagePrefix) { "$imagePrefix/napcatqq-service-backend:$Tag" } else { "napcatqq-service-backend:$Tag" }
$frontendImage = if ($imagePrefix) { "$imagePrefix/napcatqq-service-frontend:$Tag" } else { "napcatqq-service-frontend:$Tag" }

Write-Host "[信息] 构建后端镜像：$backendImage" -ForegroundColor Cyan
Invoke-DockerCommand -Arguments @('build', '--provenance=false', '--platform', $Platform, '-t', $backendImage, '-f', 'backend/Dockerfile', '.')
Write-Host "[信息] 构建前端镜像：$frontendImage" -ForegroundColor Cyan
Invoke-DockerCommand -Arguments @('build', '--provenance=false', '--platform', $Platform, '-t', $frontendImage, '-f', 'frontend/Dockerfile', '.')

if ($Push) {
    Invoke-DockerCommand -Arguments @('push', $backendImage)
    Invoke-DockerCommand -Arguments @('push', $frontendImage)
}

Write-Host '[成功] 镜像构建完成。' -ForegroundColor Green
