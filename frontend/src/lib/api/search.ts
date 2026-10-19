/**
 * 统一搜索（backend/app/modules/search/router.py，beta16 §22）。
 *
 * 三个端点均允许匿名访问；`href` 由后端给出，前端不再自行拼接站点域名。
 * 旧的资源搜索（带学院、培养方案、教师、学年筛选）已迁至 `/search/resources`，
 * 见 `interaction.ts` 的 `search()`。
 */
import { api } from './client';

export type SearchScope = 'all' | 'course' | 'resource' | 'creator';
export type SuggestType = 'course' | 'resource' | 'creator';

export interface CourseHit {
  id: string;
  name: string;
  code: string | null;
  college_name: string | null;
}

export interface ResourceHit {
  id: string;
  title: string;
  resource_type: string;
  course_id: string;
  course_name: string;
}

export interface CreatorHit {
  id: string;
  display_name: string;
  resource_count: number;
}

/** 三组独立返回；`total` 是本次实际返回条数之和，不是全库命中数。 */
export interface UnifiedSearchResult {
  query: string;
  scope: SearchScope;
  courses: CourseHit[];
  resources: ResourceHit[];
  creators: CreatorHit[];
  total: number;
}

export interface SuggestItem {
  type: SuggestType;
  id: string;
  label: string;
  sublabel: string | null;
  href: string;
}

export interface SuggestResult {
  items: SuggestItem[];
}

export interface HotItem {
  query: string;
  count: number;
}

export interface HotResult {
  items: HotItem[];
  window_days: number;
}

export async function unifiedSearch(params: {
  q: string;
  scope?: SearchScope;
  limit?: number;
}): Promise<UnifiedSearchResult> {
  const { data } = await api.get<UnifiedSearchResult>('/search', {
    params: {
      q: params.q,
      scope: params.scope ?? 'all',
      limit: params.limit ?? 10,
    },
  });
  return data;
}

/** 输入联想：前缀优先，后端不写查询日志，可按按键频率调用（前端仍需防抖）。 */
export async function suggest(q: string): Promise<SuggestResult> {
  const { data } = await api.get<SuggestResult>('/search/suggest', {
    params: { q },
  });
  return data;
}

/** 窗口内热词；`window_days` 用于向用户解释统计口径。 */
export async function hotQueries(): Promise<HotResult> {
  const { data } = await api.get<HotResult>('/search/hot');
  return data;
}
