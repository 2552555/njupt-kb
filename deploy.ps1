# ============================================
# 南邮知识库 — Windows PowerShell 部署脚本
# 使用: .\deploy.ps1
# ============================================

Write-Host ""
Write-Host "  ╔══════════════════════════════════════╗" -ForegroundColor Green
Write-Host "  ║    📚 南邮知识库 - 生产部署          ║" -ForegroundColor Green
Write-Host "  ║    NJUPT Knowledge Base              ║" -ForegroundColor Green
Write-Host "  ╚══════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""

# 检查 Docker
$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) {
    Write-Host "[ERROR] 未安装 Docker Desktop，请先安装" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] Docker 已安装" -ForegroundColor Green

# 检查 .env
if (-not (Test-Path .env)) {
    Write-Host "[WARN] 未找到 .env 文件，从 .env.production 复制..." -ForegroundColor Yellow
    Copy-Item .env.production .env
    Write-Host "请编辑 .env 文件修改密码和密钥，然后重新运行此脚本" -ForegroundColor Yellow
    exit 1
}

# 检查是否修改了默认密码
$envContent = Get-Content .env -Raw
if ($envContent -match "请修改为强密码") {
    Write-Host "[ERROR] 请先编辑 .env 文件，修改数据库密码和 JWT 密钥！" -ForegroundColor Red
    exit 1
}

# 创建 SSL 目录
New-Item -ItemType Directory -Force -Path nginx/ssl | Out-Null

# 停止旧服务
Write-Host "[1/4] 停止旧服务..." -ForegroundColor Yellow
docker compose -f docker-compose.prod.yml down 2>$null

# 构建镜像
Write-Host "[2/4] 构建 Docker 镜像..." -ForegroundColor Yellow
docker compose -f docker-compose.prod.yml build --no-cache

# 启动
Write-Host "[3/4] 启动服务..." -ForegroundColor Yellow
docker compose -f docker-compose.prod.yml up -d

# 等待
Write-Host "[4/4] 等待数据库就绪..." -ForegroundColor Yellow
Start-Sleep -Seconds 10

# 迁移 + 种子
Write-Host "执行数据库迁移..." -ForegroundColor Yellow
docker compose -f docker-compose.prod.yml exec -T backend alembic upgrade head 2>$null
Write-Host "导入种子数据..." -ForegroundColor Yellow
docker compose -f docker-compose.prod.yml exec -T backend python seed.py 2>$null

# 完成
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  部署完成!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  访问: http://localhost"
Write-Host ""
Write-Host "  查看日志: docker compose -f docker-compose.prod.yml logs -f"
Write-Host "  停止服务: docker compose -f docker-compose.prod.yml down"
Write-Host ""
