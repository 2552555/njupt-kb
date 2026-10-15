import type { MetadataRoute } from 'next';

import { SERVER_API_BASE } from '@/lib/api/server';
import type { CourseListItem } from '@/lib/api/catalog';
import type { SearchItem } from '@/lib/api/interaction';
import type { CursorPage } from '@/lib/api/types';

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'http://localhost:3000';

// 定期重建即可；不列出账号、草稿、管理后台与私有资源（§19.3）。
export const revalidate = 3600;

async function fetchPublic<T>(path: string): Promise<T | null> {
  try {
    const response = await fetch(`${SERVER_API_BASE}${path}`, { cache: 'no-store' });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const entries: MetadataRoute.Sitemap = [
    { url: `${siteUrl}/`, changeFrequency: 'daily', priority: 1 },
    { url: `${siteUrl}/courses`, changeFrequency: 'daily', priority: 0.8 },
  ];

  const [courses, resources] = await Promise.all([
    fetchPublic<CursorPage<CourseListItem>>('/courses?limit=100'),
    fetchPublic<CursorPage<SearchItem>>('/search?limit=100'),
  ]);

  for (const course of courses?.items ?? []) {
    entries.push({
      url: `${siteUrl}/courses/${course.id}`,
      changeFrequency: 'weekly',
      priority: 0.7,
    });
  }

  for (const item of resources?.items ?? []) {
    entries.push({
      url: `${siteUrl}/resources/${item.resource_id}`,
      changeFrequency: 'weekly',
      priority: 0.6,
    });
  }

  return entries;
}
