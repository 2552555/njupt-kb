# 免费部署指南 — Vercel + Render

## 总览

```
用户浏览器
    │
    ├──→ Vercel (前端 Next.js) — 免费
    │      https://njupt-kb.vercel.app
    │
    └──→ Render (后端 FastAPI) — 免费
           https://njupt-kb-api.onrender.com
              │
              └──→ Neon.tech (PostgreSQL) — 免费
```

**总费用: ¥0/月**

---

## Step 1: 部署后端 + 数据库（Render + Neon）

### 1a. 创建免费 PostgreSQL（Neon.tech）
1. 打开 https://neon.tech → Sign Up (GitHub 登录)
2. Create Project → 记住连接字符串：
   ```
   postgresql://user:password@xxx.us-east-2.aws.neon.tech/njupt_kb
   ```

### 1b. 部署后端到 Render
1. 打开 https://render.com → Sign Up (GitHub 登录)
2. New → Web Service → 连接你的 GitHub 仓库
3. 配置：
   ```
   Root Directory: backend
   Build Command: pip install -r requirements.txt
   Start Command: uvicorn app.main:app --host 0.0.0.0 --port 10000
   ```
4. 添加环境变量：
   ```
   DATABASE_URL=postgresql+asyncpg://user:password@xxx.neon.tech/njupt_kb
   SECRET_KEY=随机字符串
   CORS_ORIGINS=["https://你的vercel域名.vercel.app"]
   ```
5. 等待部署完成 → 记下 URL: `https://njupt-kb-api.onrender.com`

### 1c. 导入种子数据（本地执行）
```bash
cd njupt-kb/backend
DATABASE_URL=postgresql+asyncpg://user:password@xxx.neon.tech/njupt_kb python seed.py
```

---

## Step 2: 部署前端到 Vercel

### 2a. 推送代码到 GitHub
```bash
cd njupt-kb
git init
git add -A
git commit -m "南邮知识库"
git remote add origin https://github.com/你的用户名/njupt-kb.git
git push -u origin main
```

### 2b. 导入到 Vercel
1. 打开 https://vercel.com → Sign Up (GitHub 登录)
2. Add New → Project → 选择 njupt-kb 仓库
3. 配置：
   ```
   Root Directory: frontend
   Framework: Next.js
   ```
4. 添加环境变量：
   ```
   NEXT_PUBLIC_API_URL=https://njupt-kb-api.onrender.com
   ```
5. Deploy → 获得 URL: `https://njupt-kb.vercel.app`

---

## 免费额度说明

| 服务 | 免费额度 | 够用吗 |
|------|---------|--------|
| **Vercel** | 100GB 带宽/月, 6000分钟构建/月 | ✅ 够几百人用 |
| **Render** | 750小时/月, 0.1 CPU | ⚠️ 冷启动慢 |
| **Neon** | 0.5GB 存储, 100小时计算/月 | ✅ MVP 够用 |

---

## 常见问题

**Q: Render 后端冷启动很慢？**
A: 免费 Render 15分钟无请求会休眠，首次访问需30-60秒唤醒。
   解决：用 uptimerobot.com 每10分钟 ping 一次保活。

**Q: 国内访问慢？**
A: Vercel 默认域名在国内可能慢。两个方案：
   1. 绑定自己的备案域名到 Vercel
   2. 后续迁到国内服务器（docker-compose.prod.yml 已备好）

**Q: 怎么更新？**
A: `git push` → Vercel 自动重新部署。后端需要手动在 Render 点 "Deploy"。
