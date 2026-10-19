# beta3：从当前目录配置构建和迁移；不会导入演示账号。
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw '请先安装并启动 Docker' }
if (-not (Test-Path -LiteralPath '.env')) { throw '请先根据 DEPLOY.md 配置 .env、私有存储和 HTTPS 入口' }

function Invoke-Compose {
    & docker compose -f docker-compose.prod.yml @args
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose 失败，退出码 $LASTEXITCODE；部署已停止" }
}

Invoke-Compose config --quiet
Invoke-Compose build
Invoke-Compose up -d --wait postgres redis minio clamav
Invoke-Compose run --rm --no-deps api alembic upgrade head
Invoke-Compose up -d api worker beat web nginx
Write-Host '服务已启动；请确认私有桶和 HTTPS 已准备，再按 DEPLOY.md 执行实际流程验收。'
