'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { FormEvent, useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { Search } from 'lucide-react';

import { Notice } from '@/components/common/Notice';
import { ResourceCard } from '@/components/resource/ResourceCard';
import { Button } from '@/components/ui/Button';
import { SkeletonList } from '@/components/ui/Skeleton';
import { describeError } from '@/lib/api/client';
import { search } from '@/lib/api/interaction';
import {
  isResourceType,
  RESOURCE_TYPE_LABELS,
  RESOURCE_TYPE_ORDER,
  type ResourceType,
} from '@/lib/api/types';

export function SearchClient() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const q = searchParams.get('q') ?? '';
  const resourceTypeParam = searchParams.get('resource_type') ?? '';
  const yearParam = searchParams.get('applicable_year') ?? '';
  const resourceType = isResourceType(resourceTypeParam) ? resourceTypeParam : '';
  const year = /^\d{4}$/.test(yearParam) ? Number(yearParam) : undefined;

  const [keyword, setKeyword] = useState(q);
  const [type, setType] = useState(resourceType);
  const [yearInput, setYearInput] = useState(yearParam);

  const activeFilters = Boolean(q || resourceType || year);

  const query = useInfiniteQuery({
    queryKey: ['search', q, resourceType, year],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      search({
        q: q || undefined,
        resource_type: (resourceType || undefined) as ResourceType | undefined,
        applicable_year: year,
        cursor: pageParam,
        limit: 20,
      }),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const next = new URLSearchParams();
    if (keyword.trim()) next.set('q', keyword.trim());
    if (type) next.set('resource_type', type);
    if (/^\d{4}$/.test(yearInput)) next.set('applicable_year', yearInput);
    const queryString = next.toString();
    router.push(queryString ? `/search?${queryString}` : '/search');
  }

  const items = query.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <div className="space-y-6">
      <header className="bg-cyan px-6 py-8 text-on-color">
        <p className="dn-kicker">SEARCH</p>
        <h1 className="dn-display mt-2 text-3xl">搜索资料</h1>
        <p className="mt-2 max-w-2xl text-on-color">
          输入课程名、课程代码、简称、资料标题或教师姓名；默认只返回你有权限查看的已发布版本。
        </p>
      </header>

      <form
        onSubmit={applyFilters}
        className="grid gap-3 border border-divider bg-surface p-4 md:grid-cols-[1fr_auto_auto_auto]"
      >
        <div>
          <label htmlFor="search-q" className="mb-1 block text-sm font-medium text-text-primary">
            关键词
          </label>
          <input
            id="search-q"
            type="search"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder="例如：模电、高数下、数构期末"
            className="min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary placeholder:text-text-secondary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
          />
        </div>
        <div>
          <label htmlFor="search-type" className="mb-1 block text-sm font-medium text-text-primary">
            资料类型
          </label>
          <select
            id="search-type"
            value={type}
            onChange={(event) => setType(event.target.value)}
            className="min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
          >
            <option value="">全部类型</option>
            {RESOURCE_TYPE_ORDER.map((value) => (
              <option key={value} value={value}>
                {RESOURCE_TYPE_LABELS[value]}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="search-year" className="mb-1 block text-sm font-medium text-text-primary">
            适用年份
          </label>
          <input
            id="search-year"
            inputMode="numeric"
            value={yearInput}
            onChange={(event) => setYearInput(event.target.value.replace(/\D/g, '').slice(0, 4))}
            placeholder="例如 2024"
            className="min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary placeholder:text-text-secondary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
          />
        </div>
        <div className="flex items-end gap-2">
          <button
            type="submit"
            className="inline-flex min-h-11 items-center gap-2 bg-brand px-4 text-sm font-medium text-on-color hover:bg-brand/90"
          >
            <Search className="h-4 w-4" aria-hidden="true" />
            搜索
          </button>
          {activeFilters && (
            <button
              type="button"
              onClick={() => {
                setKeyword('');
                setType('');
                setYearInput('');
                router.push('/search');
              }}
              className="inline-flex min-h-11 items-center border border-control-border px-4 text-sm font-medium text-text-primary hover:bg-canvas"
            >
              清除
            </button>
          )}
        </div>
      </form>

      {!q && !activeFilters ? (
        <Notice tone="info" title="输入关键词开始搜索">
          也可以先浏览课程目录，再进入课程页按类型查找资料。
        </Notice>
      ) : query.isLoading ? (
        <SkeletonList count={3} />
      ) : query.error ? (
        <Notice
          tone="warning"
          title="搜索暂时无法完成"
          action={
            <Button variant="secondary" onClick={() => query.refetch()}>
              重试
            </Button>
          }
        >
          {describeError(query.error).message}。这不表示没有资料。
        </Notice>
      ) : items.length === 0 ? (
        <Notice tone="info" title="没有匹配的资料">
          可以换一个关键词，或放宽资料类型与年份筛选。
        </Notice>
      ) : (
        <>
          <p className="text-sm text-text-secondary">已显示 {items.length} 条结果</p>
          <div className="grid gap-4 sm:grid-cols-2">
            {items.map((item) => (
              <ResourceCard
                key={item.resource_id}
                resourceId={item.resource_id}
                title={item.title}
                resourceType={item.resource_type}
                courseName={item.course?.name ?? null}
                courseCode={item.course?.code ?? null}
                freshness={item.freshness_status}
                rating={item.rating}
              />
            ))}
          </div>
          {query.hasNextPage && (
            <div className="flex justify-center pt-2">
              <Button
                variant="secondary"
                onClick={() => query.fetchNextPage()}
                isLoading={query.isFetchingNextPage}
              >
                加载更多
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
