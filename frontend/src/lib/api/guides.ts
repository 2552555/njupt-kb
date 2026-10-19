/**
 * 课程攻略文章接口（backend/app/modules/content/guides_router.py）。
 * 文章是「可编辑 + 版本审核」的内容，与上传的资料文件是两种类型。
 */
import { api } from './client';
import type { CursorPage } from './types';

export type GuideStatus = 'draft' | 'pending' | 'published' | 'rejected' | 'withdrawn';

export interface GuideArticle {
  id: string;
  course_id: string;
  author_id: string;
  title: string;
  summary: string | null;
  body_markdown: string;
  status: GuideStatus;
  version_no: number;
  source: 'draft' | 'latest' | 'published';
  published_at: string | null;
  created_at: string;
  updated_at: string;
  revision: number;
}

export interface GuideListItem {
  id: string;
  course_id: string;
  author_id: string;
  title: string;
  summary: string | null;
  status: GuideStatus;
  published_version_no: number | null;
  published_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface GuidePendingItem {
  article_id: string;
  version_no: number;
  title: string;
  summary: string | null;
  author_id: string;
  course_id: string;
  submitted_at: string | null;
}

export async function createGuide(input: {
  course_id: string;
  title: string;
  summary?: string;
  body_markdown: string;
}): Promise<GuideArticle> {
  const { data } = await api.post<GuideArticle>('/guides', input);
  return data;
}

export async function listGuides(
  courseId: string,
  cursor?: string,
): Promise<CursorPage<GuideListItem>> {
  const { data } = await api.get<CursorPage<GuideListItem>>('/guides', {
    params: { course_id: courseId, cursor, limit: 12 },
  });
  return data;
}

export async function getGuide(id: string): Promise<GuideArticle> {
  const { data } = await api.get<GuideArticle>(`/guides/${id}`);
  return data;
}

export async function updateGuide(
  id: string,
  input: {
    expected_revision: number;
    title?: string;
    summary?: string;
    body_markdown?: string;
  },
): Promise<GuideArticle> {
  const { data } = await api.patch<GuideArticle>(`/guides/${id}`, input);
  return data;
}

export async function submitGuide(id: string): Promise<GuideArticle> {
  const { data } = await api.post<GuideArticle>(`/guides/${id}/submit`);
  return data;
}

export async function withdrawGuide(id: string, reason: string): Promise<GuideArticle> {
  const { data } = await api.post<GuideArticle>(`/guides/${id}/withdraw`, { reason });
  return data;
}

export async function listMyGuides(): Promise<{ items: GuideListItem[]; has_more: boolean; total: number }> {
  const { data } = await api.get<{ items: GuideListItem[]; has_more: boolean; total: number }>(
    '/me/guides',
    { params: { limit: 20 } },
  );
  return data;
}

export async function listPendingGuides(): Promise<{ items: GuidePendingItem[]; total: number }> {
  const { data } = await api.get<{ items: GuidePendingItem[]; total: number }>(
    '/admin/guides/pending',
  );
  return data;
}

export async function reviewGuide(
  id: string,
  input: { version_no: number; approve: boolean; note?: string },
): Promise<GuideArticle> {
  const { data } = await api.post<GuideArticle>(`/admin/guides/${id}/decisions`, input);
  return data;
}
