import type { NextConfig } from "next";

const API_URL = process.env.API_INTERNAL_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const nextConfig: NextConfig = {
  turbopack: {
    root: __dirname,
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_URL}/api/:path*`,
      },
      {
        source: "/uploads/:path*",
        destination: `${API_URL}/uploads/:path*`,
      },
    ];
  },
  // Production: standalone output for Docker
  output: "standalone",
  productionBrowserSourceMaps: false,
  // 浏览器侧安全响应头（补充规范 §27/§28）。API 的严格 CSP 由后端中间件下发；
  // 前端 HTML/JS 需要自身可执行的 CSP，故此处不设置 default-src 'none'（否则会破坏 Next.js 渲染），
  // 前端 CSP 应由前端另行按资源白名单配置。HSTS 仅在 production 生效。
  async headers() {
    const isProd = process.env.NODE_ENV === "production";
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Content-Security-Policy", value: "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'" },
          { key: "Referrer-Policy", value: "no-referrer" },
          {
            key: "Permissions-Policy",
            value: "geolocation=(), camera=(), microphone=(), payment=()",
          },
          { key: "X-XSS-Protection", value: "0" },
          ...(isProd
            ? [
                {
                  key: "Strict-Transport-Security",
                  value: "max-age=31536000; includeSubDomains",
                },
              ]
            : []),
        ],
      },
    ];
  },
};

export default nextConfig;
