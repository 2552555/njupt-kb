# 南邮知识库 — 生产部署指南

## 架构概览

```
浏览器 → Nginx (:80/:443) → Next.js (:3000) 前端
                           → FastAPI (:8000) 后端 → PostgreSQL (:5432)
                                                   → Redis (:6379)
```

## 前置条件

- Docker 20+ & Docker Compose v2
- 域名 (如 `njupt-kb.example.com`) + DNS 解析到服务器 IP
- （可选）SSL 证书 (Let's Encrypt / 阿里云)

## 快速部署（5 步）

### Step 1: 配置环境变量

```bash
cd ~/njupt-kb
cp .env.production .env
# 编辑 .env，修改:
#   - SECRET_KEY (生成: openssl rand -hex 32)
#   - DB_PASSWORD (PostgreSQL 密码)
#   - DOMAIN (你的域名)
```

### Step 2: 启动全部服务

```bash
docker compose -f docker-compose.prod.yml up -d
```

### Step 3: 运行数据库迁移

```bash
# 进入后端容器
docker compose -f docker-compose.prod.yml exec backend bash

# 执行迁移
alembic upgrade head

# (可选) 导入种子数据
python seed.py

exit
```

### Step 4: 验证

```bash
# 检查服务状态
docker compose -f docker-compose.prod.yml ps

# 测试 API
curl http://localhost/api/health

# 测试前端
curl http://localhost
```

### Step 5: （可选）配置 HTTPS

1. 申请 SSL 证书放到 `nginx/ssl/` 目录：
   - `fullchain.pem` — 证书链
   - `privkey.pem` — 私钥

2. 编辑 `nginx/nginx.conf`，取消注释 HTTPS server block

3. 重启 Nginx：
   ```bash
   docker compose -f docker-compose.prod.yml restart nginx
   ```

## 常用运维命令

```bash
# 查看日志
docker compose -f docker-compose.prod.yml logs -f backend

# 重启单个服务
docker compose -f docker-compose.prod.yml restart backend

# 更新应用
git pull
docker compose -f docker-compose.prod.yml up -d --build

# 备份数据库
docker compose -f docker-compose.prod.yml exec db pg_dump -U njupt njupt_kb > backup.sql

# 恢复数据库
docker compose -f docker-compose.prod.yml exec -T db psql -U njupt njupt_kb < backup.sql

# 查看资源占用
docker stats
```

## 数据库迁移指南

### 从 SQLite 迁移到 PostgreSQL

1. 导出 SQLite 数据：
   ```bash
   cd backend
   sqlite3 njupt_kb.db .dump > dump.sql
   ```

2. 转换为 PostgreSQL 兼容格式（手动或使用 `pgloader`）

3. 导入 PostgreSQL：
   ```bash
   docker compose -f docker-compose.prod.yml exec -T db psql -U njupt njupt_kb < dump_pg.sql
   ```

### 使用 Alembic 管理迁移

```bash
# 创建新迁移
docker compose -f docker-compose.prod.yml exec backend alembic revision --autogenerate -m "描述"

# 执行迁移
docker compose -f docker-compose.prod.yml exec backend alembic upgrade head

# 回滚
docker compose -f docker-compose.prod.yml exec backend alembic downgrade -1
```

## 域名 + HTTPS 配置清单

- [ ] 域名已购买并备案（如使用国内服务器）
- [ ] DNS A 记录指向服务器 IP
- [ ] SSL 证书已申请（Let's Encrypt / 阿里云免费证书）
- [ ] `nginx/ssl/` 目录已放置证书文件
- [ ] `nginx/nginx.conf` HTTPS server block 已启用
- [ ] 防火墙开放 80/443 端口
- [ ] `.env` 中 `DOMAIN` 已设置为真实域名
- [ ] `CORS_ORIGINS` 包含域名

## 性能优化建议

1. **CDN 加速**：静态资源通过阿里云 DCDN 分发
2. **数据库连接池**：PostgreSQL `pool_size=20`
3. **Redis 缓存**：热点数据（排行榜、首页）缓存 5 分钟
4. **Next.js ISR**：配置 `revalidate` 定时重新生成页面
5. **Gzip 压缩**：Nginx 已默认开启
6. **图片优化**：使用 Next.js `<Image>` 组件

## 安全清单

- [ ] `SECRET_KEY` 已改为随机字符串
- [ ] `DB_PASSWORD` 不是默认密码
- [ ] `.env` 文件已加入 `.gitignore`
- [ ] HTTPS 已启用
- [ ] 防火墙仅开放 80/443
- [ ] 文件上传大小限制已配置
- [ ] CORS 白名单已设置
