'use client';

import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { SkeletonList } from '@/components/ui/Skeleton';
import { describeError } from '@/lib/api/client';
import { listMyCorrections, listMyRequests } from '@/lib/api/interaction';
import { formatDateTime } from '@/lib/utils';

export default function MyResourcesPage() {
  const correctionsQuery = useQuery({
    queryKey: ['my-corrections'],
    queryFn: () => listMyCorrections(),
  });
  const requestsQuery = useQuery({
    queryKey: ['my-requests'],
    queryFn: () => listMyRequests(),
  });

  return (
    <div className="space-y-6">
      {/* 后端尚未提供「我的资料」列表接口，界面明确说明不可用，不做假按钮（§25.2、§26.2）。 */}
      <Notice tone="warning" title="「我的资料」列表暂时不可用">
        当前后端未提供按作者列出草稿、审核中、已发布与已驳回资料的接口，因此这里无法展示你的资料清单，
        也不会用占位数据代替。你仍然可以上传新资料，并在这里查看纠错与求资料记录。
      </Notice>

      <div className="flex flex-wrap gap-3">
        <Link
          href="/upload"
          className="inline-flex min-h-11 items-center bg-brand px-4 text-sm font-medium text-on-color hover:bg-brand/90"
        >
          上传新资料
        </Link>
        <Link
          href="/me/favorites"
          className="inline-flex min-h-11 items-center border border-control-border px-4 text-sm font-medium text-text-primary hover:bg-canvas"
        >
          我的收藏
        </Link>
      </div>

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
                  <p className="text-sm text-text-primary">{item.description}</p>
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
              在资料详情页可以使用「举报或纠错」提交问题。
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
          ) : requestsQuery.data && requestsQuery.data.items.length > 0 ? (
            <ul className="space-y-3">
              {requestsQuery.data.items.map((item) => (
                <li key={item.id} className="border border-divider bg-surface p-4">
                  <p className="text-sm text-text-primary">{item.description}</p>
                  <p className="mt-1 text-xs text-text-secondary">
                    状态：{item.status} · {item.follower_count} 人关注 · 发布于{' '}
                    {formatDateTime(item.created_at)}
                  </p>
                </li>
              ))}
            </ul>
          ) : (
            <Notice tone="info" title="还没有发布过资料需求">
              资料需求功能属于后续阶段，接口可用后这里会显示你的记录。
            </Notice>
          )}
        </div>
      </section>
    </div>
  );
}
