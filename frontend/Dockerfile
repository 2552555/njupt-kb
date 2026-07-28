# ============================================
# 南邮知识库 — 前端生产镜像 (Next.js Standalone)
# 构建: docker build -t njupt-kb-frontend .
# ============================================

FROM node:22-alpine AS base
WORKDIR /app

# 依赖层 (利用 Docker 缓存)
FROM base AS deps
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

# 构建层
FROM base AS builder
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
RUN npm run build

# 运行层 (最小镜像)
FROM base AS runner
ENV NODE_ENV=production

RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

CMD ["node", "server.js"]
