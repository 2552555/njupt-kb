#!/bin/bash
# ============================================
# 南邮知识库 — 一键部署脚本
# 使用: chmod +x deploy.sh && ./deploy.sh
# ============================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}"
echo "  ╔══════════════════════════════════════╗"
echo "  ║    📚 南邮知识库 - 生产部署          ║"
echo "  ║    NJUPT Knowledge Base              ║"
echo "  ╚══════════════════════════════════════╝"
echo -e "${NC}"

# ---- 检查 Docker ----
if ! command -v docker &> /dev/null; then
    echo -e "${RED}[ERROR] 未安装 Docker，请先安装: curl -fsSL https://get.docker.com | sh${NC}"
    exit 1
fi

if ! docker compose version &> /dev/null; then
    echo -e "${RED}[ERROR] 需要 Docker Compose v2+${NC}"
    exit 1
fi

echo -e "${GREEN}[OK] Docker $(docker --version | cut -d' ' -f3 | cut -d',' -f1)${NC}"

# ---- 检查 .env ----
if [ ! -f .env ]; then
    echo -e "${YELLOW}[WARN] 未找到 .env 文件，从 .env.production 复制...${NC}"
    cp .env.production .env
    echo -e "${YELLOW}请编辑 .env 文件修改密码和密钥，然后重新运行此脚本${NC}"
    exit 1
fi

# 检查是否修改了默认密码
if grep -q "请修改为强密码" .env 2>/dev/null; then
    echo -e "${RED}[ERROR] 请先编辑 .env 文件，修改数据库密码和 JWT 密钥！${NC}"
    exit 1
fi

source .env

# ---- 创建 SSL 目录 ----
mkdir -p nginx/ssl

# ---- 停止旧容器 ----
echo -e "${YELLOW}[1/4] 停止旧服务...${NC}"
docker compose -f docker-compose.prod.yml down 2>/dev/null || true

# ---- 构建镜像 ----
echo -e "${YELLOW}[2/4] 构建 Docker 镜像...${NC}"
docker compose -f docker-compose.prod.yml build --no-cache

# ---- 启动服务 ----
echo -e "${YELLOW}[3/4] 启动服务...${NC}"
docker compose -f docker-compose.prod.yml up -d

# ---- 等待数据库就绪 ----
echo -e "${YELLOW}[4/4] 等待数据库就绪...${NC}"
sleep 10

# ---- 数据库迁移 ----
echo -e "${YELLOW}执行数据库迁移...${NC}"
docker compose -f docker-compose.prod.yml exec -T backend alembic upgrade head 2>/dev/null || \
    echo -e "${YELLOW}[WARN] 迁移可能已执行过，跳过${NC}"

# ---- 导入种子数据 ----
echo -e "${YELLOW}导入种子数据...${NC}"
docker compose -f docker-compose.prod.yml exec -T backend python seed.py 2>/dev/null || \
    echo -e "${YELLOW}[WARN] 种子数据可能已存在，跳过${NC}"

# ---- 完成 ----
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  部署完成！${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "  访问地址: http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'YOUR_SERVER_IP')"
echo ""
echo "  常用命令:"
echo "    查看日志:  docker compose -f docker-compose.prod.yml logs -f"
echo "    重启服务:  docker compose -f docker-compose.prod.yml restart"
echo "    停止服务:  docker compose -f docker-compose.prod.yml down"
echo "    备份数据:  docker compose -f docker-compose.prod.yml exec db pg_dump -U ${DB_USER:-njupt} ${DB_NAME:-njupt_kb} > backup.sql"
echo ""
