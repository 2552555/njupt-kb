'use client';

import Link from 'next/link';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { SkeletonList } from '@/components/ui/Skeleton';
import { describeError } from '@/lib/api/client';
import { listMyCorrections, listMyRequests } from '@/lib/api/interaction';
import { formatDateTime } from '@/lib/utils';
import { useAuthStore } from '@/stores/authStore';

export function ContributionHistory() {
  const user = useAuthStore(s => s.user);
  const correctionsQuery = useQuery({
    queryKey: ['my-corrections', user?.id],
    queryFn: () => listMyCorrections(),
  });
  const requestsQuery = useInfiniteQuery({
    initialPageParam: null as string | null,
    queryKey: ['my-requests', user?.id],
    queryFn: ({pageParam}) => listMyRequests(pageParam),
    getNextPageParam: page => page.has_more ? page.next_cursor : undefined,
  });

  return (
    <div className="space-y-6">
      <section aria-labelledby="my-corrections">
        <h2 id="my-corrections" className="dn-display text-2xl">
          我提交的纠错
        </h2>
        <div className="mt-3">
          {correctionsQuery.isLoading ? (
            <SkeletonList count={2} />
          ) : correctionsQuery.error ? (
            <Notice
              tone="warning"
              title="纠错记录暂时无法加载"
              action={<Button variant="secondary" onClick={() => correctionsQuery.refetch()}>重试</Button>}
            >
              {describeError(correctionsQuery.error).message}
            </Notice>
          ) : correctionsQuery.data && correctionsQuery.data.items.length > 0 ? (
            <ul className="space-y-3">
              {correctionsQuery.data.items.map((item) => (
                <li key={item.id} className="border border-divider bg-surface p-4">
                  <p className="text-sm text-text-primary">{item.description}</p><Link className="inline-flex min-h-11 items-center text-brand underline" href={`/resources/${item.resource_id}`}>查看资料</Link>
                  <p className="mt-1 text-xs text-text-secondary">
                    状态：{item.status} · 提交于 {formatDateTime(item.created_at)}
                  </p>
                  {item.resolution_note && (
                    <p className="mt-1 text-xs text-text-secondary">处理说明：{item.resolution_note}</p>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <Notice tone="info" title="还没有提交过纠错">
              在资料详情页用「举报或纠错」提交问题。
            </Notice>
          )}
        </div>
      </section>

      <section aria-labelledby="my-requests">
        <h2 id="my-requests" className="dn-display text-2xl">
          我发布的求资料
        </h2>
        <div className="mt-3">
          {requestsQuery.isLoading ? (
            <SkeletonList count={2} />
          ) : requestsQuery.error ? (
            <Notice
              tone="warning"
              title="求资料记录暂时无法加载"
              action={<Button variant="secondary" onClick={() => requestsQuery.refetch()}>重试</Button>}
            >
              {describeError(requestsQuery.error).message}
            </Notice>
          ) : requestsQuery.data && requestsQuery.data.pages.flatMap(page => page.items).length > 0 ? (
            <><ul className="space-y-3">
              {requestsQuery.data.pages.flatMap(page => page.items).map((item) => (
                <li key={item.id} className="border border-divider bg-surface p-4">
                  <p className="text-sm text-text-primary">{item.description}</p><Link className="inline-flex min-h-11 items-center text-brand underline" href={`/requests/${item.id}`}>查看与处理</Link>
                  <p className="mt-1 text-xs text-text-secondary">
                    状态：{item.status} · {item.follower_count} 人关注 · 发布于{' '}
                    {formatDateTime(item.created_at)}
                  </p>
                  {item.status === 'fulfilled' && !item.resolved_resource_accessible && (
                    <p className="mt-1 text-xs text-text-secondary">
                      原解决资料当前不可访问，可复核后重新打开。
                    </p>
                  )}
                </li>
              ))}
            </ul>{requestsQuery.hasNextPage && <Button onClick={()=>requestsQuery.fetchNextPage()} isLoading={requestsQuery.isFetchingNextPage}>更多我的需求</Button>}</>
          ) : (
            <Notice tone="info" title="还没有发布过资料需求">
              在<Link href="/requests" className="underline underline-offset-2">求资料</Link>
              中为课程提出需求。
            </Notice>
          )}
        </div>
      </section>
    </div>
  );
}
