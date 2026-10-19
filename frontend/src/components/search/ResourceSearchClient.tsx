'use client';

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { FormEvent, useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { ArrowLeft, Search } from 'lucide-react';

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

/**
 * 资料高级筛选（beta16：统一搜索迁到 `/search` 后，带学院/培养方案/教师/学年
 * 能力的资源搜索保留在此，参数与结果结构不变）。
 */
export function ResourceSearchClient() {
  const params = useSearchParams();
  return <ResourceSearchResults key={params.toString()} />;
}

function ResourceSearchResults() {
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
    queryKey: ['search-resources', q, resourceType, year],
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

  function navigateFilters(nextType = type) {
    const next = new URLSearchParams();
    if (keyword.trim()) next.set('q', keyword.trim());
    if (nextType) next.set('resource_type', nextType);
    if (/^\d{4}$/.test(yearInput)) next.set('applicable_year', yearInput);
    const queryString = next.toString();
    router.push(queryString ? `/search/resources?${queryString}` : '/search/resources');
  }

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    navigateFilters();
  }

  const items = query.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <div className="space-y-6">
      <header className="bg-cyan px-6 py-8 text-on-color">
        <p className="dn-kicker">FILTER</p>
        <h1 className="dn-display mt-2 text-3xl">在资料中筛选</h1>
        <p className="mt-2 max-w-2xl text-sm">
          按关键词、资料类型与适用年份缩小范围，结果与统一搜索一致。
        </p>
      </header>

      <Link
        href={q ? `/search?q=${encodeURIComponent(q)}` : '/search'}
        className="inline-flex min-h-11 items-center gap-2 text-sm text-brand underline"
      >
        <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        返回统一搜索
      </Link>

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
            onChange={(event) => {
              setType(event.target.value);
              navigateFilters(event.target.value);
            }}
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
                router.push('/search/resources');
              }}
              className="inline-flex min-h-11 items-center border border-control-border px-4 text-sm font-medium text-text-primary hover:bg-canvas"
            >
              清除
            </button>
          )}
        </div>
      </form>

      {query.isLoading ? (
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
          <p>可以换一个关键词，或放宽资料类型与年份筛选。</p>
          <Link
            href={q ? `/requests?new=1&keyword=${encodeURIComponent(q)}` : '/requests?new=1'}
            className="mt-2 inline-flex min-h-11 items-center underline"
          >
            求资料
          </Link>
        </Notice>
      ) : (
        <>
          <p className="text-sm text-text-secondary">已显示 {items.length} 条结果</p>
          <div className="dn-stagger grid gap-4 sm:grid-cols-2">
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
