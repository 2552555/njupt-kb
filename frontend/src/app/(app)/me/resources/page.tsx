'use client';

import Link from 'next/link';
import { useInfiniteQuery } from '@tanstack/react-query';
import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { listMyResources } from '@/lib/api/content';
import { describeError } from '@/lib/api/client';
import { formatDateTime } from '@/lib/utils';
import { ContributionHistory } from '@/components/resource/ContributionHistory';

const states = { draft: '草稿', pending: '审核中', needs_info: '需要补证', approved: '已通过', rejected: '已驳回' };

export default function MyResourcesPage() {
  const query = useInfiniteQuery({
    queryKey: ['my-resources'], initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => listMyResources(pageParam),
    getNextPageParam: (page) => page.has_more ? page.next_cursor : undefined,
  });
  return <div className="space-y-6">
    <div className="flex items-center justify-between gap-4">
      <h2 className="dn-display text-2xl">我的资料</h2>
      <Link href="/upload" className="bg-brand px-4 py-3 text-sm text-on-color">上传新资料</Link>
    </div>
    {query.isPending && <p role="status">正在加载你的资料…</p>}
    {query.error && <Notice title="资料列表加载失败" tone="warning"
      action={<Button onClick={() => query.refetch()}>重试</Button>}>{describeError(query.error).message}</Notice>}
    {query.data?.pages[0].items.length === 0 && <Notice title="还没有资料" tone="info" />}
    <ul className="space-y-3">{query.data?.pages.flatMap(page => page.items).map(item =>
      <li key={item.id} className="border border-divider bg-surface p-5">
        <div className="flex flex-wrap justify-between gap-3">
          <div><h3 className="font-semibold">{item.latest_version.title}</h3>
            <p className="mt-2 text-sm text-text-secondary">V{item.latest_version.version_no} · {states[item.latest_version.review_status]} · {formatDateTime(item.updated_at)}</p>
            {item.publication_status === 'withdrawn' && <p className="text-sm">资料已撤下</p>}
          </div>
          <Link href={`/me/resources/${item.id}/versions/${item.latest_version.id}`}
            className="self-center border border-control-border px-4 py-3 text-sm">查看与管理</Link>
        </div>
      </li>)}</ul>
    {query.hasNextPage && <Button variant="secondary" isLoading={query.isFetchingNextPage}
      onClick={() => query.fetchNextPage()}>加载更多</Button>}
    <ContributionHistory />
  </div>;
}
