import type { Metadata } from 'next';
import Link from 'next/link';

import { Notice } from '@/components/common/Notice';
import type { ResourceListItem, ResourceListOut } from '@/lib/api/content';
import { serverFetch } from '@/lib/api/server';
import { isResourceType, RESOURCE_TYPE_LABELS, RESOURCE_TYPE_ORDER } from '@/lib/api/types';
import { firstSearchParam, formatRelative } from '@/lib/utils';

export const metadata: Metadata = {
  title: '资料',
  description: '按最新或热门浏览南邮课程知识平台的公开资料。',
  alternates: { canonical: '/resources' },
};

export const dynamic = 'force-dynamic';

type ResourceSort = 'latest' | 'oldest' | 'hot';

const SORT_LABELS: Record<ResourceSort, string> = {
  latest: '最新',
  oldest: '最早',
  hot: '热门',
};

type ResourcesSearchParams = Partial<Record<'sort' | 'course_id' | 'keyword' | 'resource_type' | 'cursor', string | string[]>>;

function buildQuery(params: Record<string, string | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) search.set(key, value);
  }
  const query = search.toString();
  return query ? `?${query}` : '';
}

function normalizeSort(value: string | undefined): ResourceSort {
  // 非法排序值回退默认，与后端 400 契约区分：URL 手输不等于接口调用。
  return value === 'hot' || value === 'oldest' ? value : 'latest';
}

export default async function ResourcesPage({
  searchParams,
}: {
  searchParams: Promise<ResourcesSearchParams>;
}) {
  const params = Object.fromEntries(Object.entries(await searchParams).map(([key, value]) => [key, firstSearchParam(value)]));
  const sort = normalizeSort(params.sort);
  const keyword = params.keyword?.trim() || '';
  const cursor = params.cursor || '';
  const resourceType = params.resource_type && isResourceType(params.resource_type) ? params.resource_type : '';
  const filters = {
    course_id: params.course_id || undefined,
    keyword: keyword || undefined,
    resource_type: resourceType || undefined,
  };

  const query = buildQuery({
    ...filters,
    sort: sort === 'latest' ? undefined : sort,
    course_id: params.course_id || undefined,
    keyword: keyword || undefined,
    cursor: cursor || undefined,
    limit: '20',
  });
  const listResult = await serverFetch<ResourceListOut>(`/resources${query}`);
  const items: ResourceListItem[] = listResult.data?.items ?? [];
  const nextCursor = listResult.data?.next_cursor ?? null;

  // 分段排序控件为原生链接：不依赖 JS 即可切换，键盘可达（§39 A20）。
  const sortLinks = (Object.keys(SORT_LABELS) as ResourceSort[]).map((value) => ({
    value,
    href: `/resources${buildQuery({
      ...filters,
      sort: value === 'latest' ? undefined : value,
      course_id: params.course_id || undefined,
      keyword: keyword || undefined,
    })}`,
  }));

  return (
    <div className="space-y-6">
      <header className="bg-ink px-6 py-8 text-on-ink">
        <p className="dn-kicker">RESOURCES</p>
        <h1 className="dn-display mt-2 text-3xl">资料</h1>
      </header>

      <form action="/resources" className="grid gap-3 border border-divider bg-surface p-4 sm:grid-cols-[1fr_auto_auto]">
        <input type="hidden" name="sort" value={sort} />
        {params.course_id && <input type="hidden" name="course_id" value={params.course_id} />}
        <label className="text-sm font-medium">
          关键词
          <input type="search" name="keyword" defaultValue={keyword} className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3" />
        </label>
        <label className="text-sm font-medium">
          资料类型
          <select name="resource_type" defaultValue={resourceType} className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3">
            <option value="">全部类型</option>
            {RESOURCE_TYPE_ORDER.map((value) => <option key={value} value={value}>{RESOURCE_TYPE_LABELS[value]}</option>)}
          </select>
        </label>
        <div className="flex items-end gap-3">
          <button type="submit" className="min-h-11 bg-brand px-4 text-sm font-medium text-on-color">筛选</button>
          {(keyword || resourceType || params.course_id) && <Link href="/resources" className="min-h-11 content-center text-sm underline">清除</Link>}
        </div>
      </form>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2" role="group" aria-label="排序方式">
          {sortLinks.map((link) => (
            <Link
              key={link.value}
              href={link.href}
              aria-current={sort === link.value ? 'true' : undefined}
              className={`inline-flex min-h-11 items-center px-3 text-sm font-medium ${
                sort === link.value
                  ? 'bg-brand text-on-color'
                  : 'border border-control-border text-text-primary hover:bg-canvas'
              }`}
            >
              {SORT_LABELS[link.value]}
            </Link>
          ))}
        </div>
      </div>

      {listResult.error ? (
        <Notice
          tone="warning"
          title="资料列表暂时无法加载"
          action={
            <Link
              href={`/resources${buildQuery({
                ...filters,
                sort: sort === 'latest' ? undefined : sort,
                course_id: params.course_id || undefined,
                keyword: keyword || undefined,
              })}`}
              className="inline-flex min-h-11 items-center border border-control-border px-3 text-sm text-text-primary hover:bg-canvas"
            >
              重试
            </Link>
          }
        >
          {listResult.error.message}
        </Notice>
      ) : items.length > 0 ? (
        <>
          <ul className="space-y-3">
            {items.map((item) => (
              <li key={item.id} className="border border-divider bg-surface p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <Link
                      href={`/resources/${item.id}`}
                      className="break-words text-sm font-medium text-text-primary underline underline-offset-2 hover:text-brand"
                    >
                      {item.title ?? '未命名资料'}
                    </Link>
                    <p className="mt-1 text-xs text-text-secondary">
                      {RESOURCE_TYPE_LABELS[item.resource_type] ?? item.resource_type}
                      {' · '}
                      {formatRelative(item.updated_at)}
                    </p>
                  </div>
                </div>
              </li>
            ))}
          </ul>
          {nextCursor && (
            <Link
              href={`/resources${buildQuery({
                ...filters,
                sort: sort === 'latest' ? undefined : sort,
                course_id: params.course_id || undefined,
                keyword: keyword || undefined,
                cursor: nextCursor,
              })}`}
              className="inline-flex min-h-11 items-center border border-control-border px-4 text-sm font-medium text-text-primary hover:bg-canvas"
            >
              下一页
            </Link>
          )}
        </>
      ) : (
        <Notice title="暂无资料">
          当前筛选条件下没有已发布的资料，可换个关键词或排序方式。
          <Link href={`/requests${buildQuery({ new: '1', keyword: keyword || undefined, course_id: params.course_id })}`} className="mt-2 flex min-h-11 items-center text-brand underline">发起资料需求</Link>
        </Notice>
      )}
    </div>
  );
}
