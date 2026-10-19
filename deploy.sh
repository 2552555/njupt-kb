#!/usr/bin/env bash
# beta3：失败即停止；不执行 .env 中的 shell 内容，不导入演示账号。
set -euo pipefail
cd -- "$(dirname -- "$0")"
command -v docker >/dev/null || { echo '请先安装并启动 Docker' >&2; exit 1; }
[[ -f .env ]] || { echo '请先根据 DEPLOY.md 配置 .env、私有存储和 HTTPS 入口' >&2; exit 1; }
docker compose -f docker-compose.prod.yml config --quiet
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d --wait postgres redis minio clamav
docker compose -f docker-compose.prod.yml run --rm --no-deps api alembic upgrade head
docker compose -f docker-compose.prod.yml up -d api worker beat web nginx
echo '服务已启动；请确认私有桶和 HTTPS 已准备，再按 DEPLOY.md 执行实际流程验收。'
