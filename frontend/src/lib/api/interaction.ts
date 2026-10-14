/**
 * 搜索、收藏、评价、评论、纠错与求资料接口（backend/app/modules/interaction/router.py）。
 * 收藏使用 PUT 设置 / DELETE 取消，不做会反转状态的 toggle（§20.1、§25.1）。
 */
import { api } from './client';
import type { CourseBrief } from './catalog';
import type { CursorPage, ResourceType } from './types';

export interface RatingSummary {
  average: number;
  count: number;
  /** 小样本采用收缩后的评分，不让单个五星无条件置顶（§20.1）。 */
  shrunk: number;
}

export interface SearchItem {
  resource_id: string;
  course_id: string;
  /** 课程摘要帮助区分同名不同课程代码的结果（§19.2）。 */
  course: CourseBrief | null;
  title: string;
  resource_type: ResourceType | null;
  freshness_status: string | null;
  published_version_id: string | null;
  rating: RatingSummary;
}

export interface FavoriteItem {
  resource_id: string;
  created_at: string;
  /** 资源下架时为 false：显示不可用状态，不自动打开旧版文件。 */
  available: boolean;
  publication_status: string;
  course_id: string | null;
  title: string | null;
}

export interface CommentItem {
  id: string;
  resource_id: string;
  user_id: string;
  display_name: string;
  parent_id: string | null;
  body: string;
  created_at: string;
}

export interface CorrectionItem {
  id: string;
  resource_id: string;
  version_id: string;
  submitter_id: string;
  description: string;
  status: string;
  resolution_note: string | null;
  fixed_version_id: string | null;
  handled_by: string | null;
  handled_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface RequestItem {
  id: string;
  course_id: string;
  scope: string | null;
  description: string;
  status: string;
  follower_count: number;
  resolved_resource_id: string | null;
  created_at: string;
}

export interface RequestCreateResult {
  created: boolean;
  request: RequestItem;
}

export interface FollowResult {
  followed: boolean;
  follower_count: number;
}

export interface CreatorPublic {
  user_id: string;
  display_name: string;
  contribution: Record<string, unknown> | null;
}

export interface SearchParams {
  q?: string;
  university_id?: string;
  curriculum_id?: string;
  course_id?: string;
  semester_id?: string;
  teacher_id?: string;
  resource_type?: ResourceType;
  applicable_year?: number;
  cursor?: string | null;
  limit?: number;
}

export async function search(params: SearchParams = {}): Promise<CursorPage<SearchItem>> {
  const { data } = await api.get<CursorPage<SearchItem>>('/search', { params });
  return data;
}

export async function setFavorite(resourceId: string): Promise<void> {
  await api.put(`/me/favorites/${resourceId}`);
}

export async function removeFavorite(resourceId: string): Promise<void> {
  await api.delete(`/me/favorites/${resourceId}`);
}

export async function listFavorites(
  cursor?: string | null,
): Promise<CursorPage<FavoriteItem>> {
  const { data } = await api.get<CursorPage<FavoriteItem>>('/me/favorites', {
    params: { cursor: cursor || undefined },
  });
  return data;
}

export async function submitRating(
  resourceId: string,
  versionId: string,
  score: number,
): Promise<RatingSummary> {
  const { data } = await api.put<RatingSummary>(
    `/resources/${resourceId}/versions/${versionId}/rating`,
    { score },
  );
  return data;
}

export async function ratingSummary(resourceId: string): Promise<RatingSummary> {
  const { data } = await api.get<RatingSummary>(
    `/resources/${resourceId}/ratings/summary`,
  );
  return data;
}

export async function listComments(
  resourceId: string,
  cursor?: string | null,
): Promise<CursorPage<CommentItem>> {
  const { data } = await api.get<CursorPage<CommentItem>>(
    `/resources/${resourceId}/comments`,
    { params: { cursor: cursor || undefined } },
  );
  return data;
}

export async function createComment(
  resourceId: string,
  body: string,
  parentId?: string | null,
): Promise<CommentItem> {
  const { data } = await api.post<CommentItem>(`/resources/${resourceId}/comments`, {
    body,
    parent_id: parentId ?? null,
  });
  return data;
}

export async function createCorrection(payload: {
  resource_id: string;
  version_id: string;
  description: string;
}): Promise<CorrectionItem> {
  const { data } = await api.post<CorrectionItem>('/corrections', payload);
  return data;
}

export async function listMyCorrections(
  cursor?: string | null,
): Promise<CursorPage<CorrectionItem>> {
  const { data } = await api.get<CursorPage<CorrectionItem>>('/me/corrections', {
    params: { cursor: cursor || undefined },
  });
  return data;
}

export async function createRequest(payload: {
  course_id: string;
  scope?: string | null;
  description: string;
}): Promise<RequestCreateResult> {
  const { data } = await api.post<RequestCreateResult>('/requests', payload);
  return data;
}

export async function listRequests(params: {
  course_id?: string;
  status?: string;
  cursor?: string | null;
} = {}): Promise<CursorPage<RequestItem>> {
  const { data } = await api.get<CursorPage<RequestItem>>('/requests', {
    params: { ...params, cursor: params.cursor || undefined },
  });
  return data;
}

export async function followRequest(requestId: string): Promise<FollowResult> {
  const { data } = await api.post<FollowResult>(`/requests/${requestId}/follow`);
  return data;
}

export async function listMyRequests(
  cursor?: string | null,
): Promise<CursorPage<RequestItem>> {
  const { data } = await api.get<CursorPage<RequestItem>>('/me/requests', {
    params: { cursor: cursor || undefined },
  });
  return data;
}

export async function creatorProfile(userId: string): Promise<CreatorPublic> {
  const { data } = await api.get<CreatorPublic>(`/creators/${userId}`);
  return data;
}
