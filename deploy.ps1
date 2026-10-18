$ErrorActionPreference = 'Stop'
chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot
$projectName = 'napcatqq-service'
$composeFile = Join-Path $projectRoot 'docker-compose.yml'
$envFile = Join-Path $projectRoot '.env'

function Invoke-DockerCommand {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker 命令执行失败: docker $($Arguments -join ' ')"
    }
}

function Get-EnvironmentValues {
    $values = @{}
    foreach ($line in Get-Content -Path $envFile -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) { continue }
        $parts = $trimmed.Split('=', 2)
        $values[$parts[0].Trim()] = $parts[1].Trim().Trim([char]34, [char]39)
    }
    return $values
}

function Get-ImageReference {
    param(
        [Parameter(Mandatory = $true)][hashtable]$EnvironmentValues,
        [Parameter(Mandatory = $true)][string]$ImageVariable,
        [Parameter(Mandatory = $true)][string]$DefaultImage
    )
    $image = if ($EnvironmentValues.ContainsKey($ImageVariable) -and $EnvironmentValues[$ImageVariable]) {
        $EnvironmentValues[$ImageVariable]
    } else {
        $DefaultImage
    }
    $tag = if ($EnvironmentValues.ContainsKey('IMAGE_TAG') -and $EnvironmentValues['IMAGE_TAG']) {
        $EnvironmentValues['IMAGE_TAG']
    } else {
        'latest'
    }
    return "$image`:$tag"
}

function Wait-ContainerHealthy {
    param(
        [Parameter(Mandatory = $true)][string]$ContainerName,
        [int]$TimeoutSeconds = 180
    )
    $start = Get-Date
    while (((Get-Date) - $start).TotalSeconds -lt $TimeoutSeconds) {
        $status = (& docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $ContainerName 2>$null).Trim()
        if ($status -eq 'healthy') {
            Write-Host "[成功] $ContainerName 健康检查通过。" -ForegroundColor Green
            return
        }
        if ($status -in @('exited', 'dead')) { throw "$ContainerName 启动失败，容器状态：$status" }
        Start-Sleep -Seconds 3
    }
    & docker compose --env-file $envFile -f $composeFile -p $projectName ps
    & docker compose --env-file $envFile -f $composeFile -p $projectName logs --no-color --tail 200 backend frontend
    throw "$ContainerName 在 ${TimeoutSeconds}s 内未通过健康检查。"
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw '未找到 Docker CLI，请先安装并启动 Docker Desktop。' }
& docker compose version *> $null
if ($LASTEXITCODE -ne 0) { throw '未找到 Docker Compose v2，请升级 Docker Desktop 或安装 Compose 插件。' }
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $projectRoot '.env.example') $envFile
    throw '首次部署已生成 .env，请填写数据库、管理员密码、Redis 和 NapCat 配置后重新执行。'
}
if (-not (Test-Path $composeFile)) { throw '未找到 docker-compose.yml。' }

$environmentValues = Get-EnvironmentValues
$requiredVariables = @(
    'DATABASE_URL',
    'ADMIN_PASSWORD',
    'SETTINGS_ENCRYPTION_SECRET'
)
$missingVariables = @(
    $requiredVariables | Where-Object {
        -not $environmentValues.ContainsKey($_) -or
        [string]::IsNullOrWhiteSpace($environmentValues[$_]) -or
        $environmentValues[$_].Contains('CHANGE_ME')
    }
)
if ($missingVariables.Count -gt 0) { throw ".env 存在缺失或未替换的必要配置：$($missingVariables -join ', ')" }

if ($environmentValues['DATABASE_URL'] -match '@(localhost|127\.0\.0\.1)(:|/)') {
    Write-Warning 'DATABASE_URL 使用 localhost/127.0.0.1；容器内地址通常应改为 host.docker.internal 或数据库服务器地址。'
}
if ($environmentValues['REDIS_URL'] -match '@(localhost|127\.0\.0\.1)(:|/)') {
    Write-Warning 'REDIS_URL 使用 localhost/127.0.0.1；容器内地址通常应改为 host.docker.internal 或 Redis 服务器地址。'
}

if (-not (Test-Path (Join-Path $projectRoot 'data'))) {
    New-Item -ItemType Directory -Path (Join-Path $projectRoot 'data') | Out-Null
}

$projectImages = @(
    (Get-ImageReference -EnvironmentValues $environmentValues -ImageVariable 'BACKEND_IMAGE' -DefaultImage 'napcatqq-service-backend'),
    (Get-ImageReference -EnvironmentValues $environmentValues -ImageVariable 'FRONTEND_IMAGE' -DefaultImage 'napcatqq-service-frontend')
)

Invoke-DockerCommand -Arguments @('info')
$composeArgs = @('--env-file', $envFile, '-f', $composeFile, '-p', $projectName)
Invoke-DockerCommand -Arguments ($composeArgs + @('config', '--quiet'))
Write-Host '[信息] 停止本项目已有容器...' -ForegroundColor Cyan
Invoke-DockerCommand -Arguments ($composeArgs + @('down', '--remove-orphans'))

foreach ($image in $projectImages) {
    & docker image inspect $image *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[信息] 删除本项目旧镜像：$image" -ForegroundColor Cyan
        Invoke-DockerCommand -Arguments @('image', 'rm', $image)
    }
}

try {
    Write-Host '[信息] 构建并启动本项目服务...' -ForegroundColor Cyan
    Invoke-DockerCommand -Arguments ($composeArgs + @('up', '-d', '--build', '--remove-orphans'))
    Wait-ContainerHealthy -ContainerName 'napcatqq-service-backend' -TimeoutSeconds 180
    Wait-ContainerHealthy -ContainerName 'napcatqq-service-frontend' -TimeoutSeconds 120
} catch {
    & docker compose @composeArgs ps
    & docker compose @composeArgs logs --no-color --tail 200 backend frontend
    throw
}

Invoke-DockerCommand -Arguments ($composeArgs + @('ps'))
$frontendPort = if ($environmentValues.ContainsKey('FRONTEND_PORT') -and $environmentValues['FRONTEND_PORT']) { $environmentValues['FRONTEND_PORT'] } else { '5178' }
Write-Host ''
Write-Host '==========================================' -ForegroundColor Green
Write-Host 'NapCatQQ Service 部署完成' -ForegroundColor Green
Write-Host '==========================================' -ForegroundColor Green
Write-Host "前端入口：http://服务器IP:$frontendPort"
Write-Host "健康检查：http://服务器IP:$frontendPort/health"
Write-Host "查看日志：docker compose --env-file .env -p $projectName logs -f"
