import type { MetadataRoute } from 'next';

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'http://localhost:3000';

/**
 * robots 只做抓取指引，不是权限机制；私有内容仍以服务端鉴权为准（§19.3）。
 */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: '*',
        allow: '/',
        disallow: ['/me/', '/admin/', '/upload', '/login', '/register', '/search'],
      },
    ],
    sitemap: `${siteUrl}/sitemap.xml`,
  };
}
