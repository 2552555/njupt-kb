# 南邮知识库 — 生产部署指南

## 0.0.2 升级补充（当前）

本次只完成本地代码发布；此节是部署步骤，不是部署记录。详细证据及门槛见 [0.0.2 发布记录](docs/RELEASE_0.0.2.md)。

1. 备份数据库、私有对象、配置及独立 MFA 密钥。核对旧 `derive/2` 任务/重试与历史派生，勿把新库测试当旧库升级。
2. 安装新的后端依赖与前端锁文件，执行一次 `alembic upgrade head` 到 **`0013_v002_mfa`**，然后再启动新 API/Worker。后端 Docker 上下文现包含迁移版本脚本。
3. 在未入库的 `.env` 配置 `MFA_ENCRYPTION_KEY`。可在可信本地环境执行 `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` 生成；不要把输出写入仓库/日志。该密钥须独立备份，丢失后不能解密种子。
4. 管理人员先在账号安全页绑定验证器并离线保存一次性恢复码，再执行受近期认证保护的敏感操作。找回密码不清除 MFA；不提供仅凭邮箱移除管理员 MFA 的入口。
5. 默认 Worker 消费全部新队列。需隔离时使用 `docker compose -f docker-compose.prod.yml -f docker-compose.workers.yml up -d`；AI profile 和模型调用开关分别控制，不能因启动 Worker 就默认允许外发。
6. 保留 `COMMERCE_ENABLED=false`、合规门槛关闭与 `AI_ENABLED=false`。保留政策未确认时保持 `RETENTION_SCHEDULE_ENABLED=false`。
7. 检查旧范围权限：它们保留但不生效，必须核对后显式重新授予全站职责；课程/资料维护仍走对象分配。切勿批量把旧范围授权自动转为全站授权。

数据库工具：`python scripts/database_backup.py backup <备份路径.dump>`；恢复到事先创建的独立空库时，设置目标 `DATABASE_URL`，执行 `python scripts/database_backup.py restore <备份路径.dump> --confirm-database <目标库名>`。工具校验备份大小和哈希、单事务恢复、不使用 `--clean`，也不重放外部付款。对象存储、密钥与数据库须共同制定恢复点并演练；本机只测试了工具逻辑。

`0013` 降级前须经已认证流程禁用已启用 MFA；`0012` 的新枚举记录需先核对，禁止为了收窄约束直接删除业务或审计事实。真实 PostgreSQL 并发、MinIO/ClamAV/Celery、Office 转换、撤权传播、TLS/WAF 与故障恢复仍需部署环境验收。

下方 beta11/beta3 为历史补充，不覆盖本节的版本、迁移及权限决定。

## beta11 升级补充

升级前执行 `alembic upgrade head`，当前迁移链到 `0008_beta11_ratelimit`。API 启动依赖新增计数表，不能先启动新应用再补迁移。

生产 `.env` 必须填写 `FORWARDED_ALLOW_IPS`：实际 Nginx 代理 IP 或其专用可信网段，逗号分隔，不使用 `*`。Compose 未填写时拒绝启动；API 不映射公网端口。当前 Nginx 按公网入口配置覆盖 XFF；若外层还有 CDN/LB，须按实际链路配置可信 real-IP 来源后再验收，不能照抄客户端自带头。

上线前分别用两种来源 IP、同校园出口下的两个登录账号验证计数隔离，确认超限得到 `429` 与 `Retry-After`；再验证真实 PDF/Office 转换、存储及扫描。限流数值通过 `RATE_LIMIT_*` 角色字典配置，与下载业务配额独立。本地测试、YAML 检查及离线迁移 SQL 不代表这些生产验收通过。

> beta3 更新：当前服务名为 `api`、`web`、`postgres`、`worker`、`beat`、`redis`、`minio`、`clamav`。
> 下文历史说明保留供迁移参考；实际部署以本节和当前 Compose 为准，不执行旧版 `backend`、`db`、`seed.py` 命令。

## beta3 部署与验证

1. 配置 `.env`：数据库、随机密钥、邮件、域名及私有存储。`CORS_ORIGINS` 使用 JSON 数组。
2. `PUBLIC_BASE_URL` 是浏览器访问的 HTTPS 地址；`API_INTERNAL_URL` 是 Web 容器访问 API 的内部地址。
3. `S3_ENDPOINT_URL` 用于服务端读写；`S3_PUBLIC_ENDPOINT_URL` 用于签名 URL，必须能被浏览器访问。两者可以相同；签名后不能再手工替换域名。
4. 配置存储端 CORS，允许站点来源的 PUT/GET 与上传所需 Content-Type；原件与隔离桶保持私有。
5. Nginx 当前文件仅启用 HTTP 示例。生产部署必须实际配置 TLS 入口与证书；`COOKIE_SECURE=true` 需要 HTTPS。仅映射 443 端口不会自动启用 TLS。

```bash
docker compose -f docker-compose.prod.yml config --quiet
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d --wait postgres redis minio clamav
docker compose -f docker-compose.prod.yml run --rm --no-deps api alembic upgrade head
# 确认私有桶已由运维创建；如需应用创建，单独授予所需权限并执行初始化。
docker compose -f docker-compose.prod.yml up -d api worker beat web nginx
```

使用托管对象存储时，按实际配置调整 MinIO 服务及准备步骤。迁移失败必须停止发布。
不默认运行 `seed_demo`；演示数据含演示账号，仅用于隔离开发环境。目录样本导入使用
`docker compose exec api python -m scripts.seed_catalog`，导入前确认内容与目标数据库。

上线后检查 `/api/v1/health/live`、`/api/v1/health/ready`，再实际验证上传、检查、预览、审核和下载。
检查 `worker`、`beat` 与 `clamav` 日志；健康检查通过不代表任务已完成。
beta3 没有新增表结构，已有 beta2 数据仍使用 `0001_initial`；新建资源会创建保守访问策略：
元数据公开、登录用户预览、原件按显式权益授权。不要在部署时自动将历史私有资料改为公开。

本地回归命令：`cd backend && python -m unittest discover -s tests -v`；测试依赖见 `requirements-dev.txt`。
浏览器契约检查见 `backend/tests/browser_beta3.py`；真实 Docker 联调仍需在具备 Docker 的环境完成。

---

## 历史部署说明（旧版，勿直接照搬）

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
