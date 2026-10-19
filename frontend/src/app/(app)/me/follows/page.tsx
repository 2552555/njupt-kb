'use client';

import Link from 'next/link';
import { useMutation, useInfiniteQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { SkeletonList } from '@/components/ui/Skeleton';
import { describeError } from '@/lib/api/client';
import { listFollows, removeFollow, type FollowItem } from '@/lib/api/interaction';
import { formatDateTime } from '@/lib/utils';
import { useAuthStore } from '@/stores/authStore';

export default function MyFollowsPage() {
  const queryClient = useQueryClient();
  const user = useAuthStore(s => s.user);
  const query = useInfiniteQuery({
    initialPageParam: null as string | null,
    queryKey: ['follows', user?.id],
    enabled: !!user,
    queryFn: ({pageParam}) => listFollows({cursor:pageParam}),
    getNextPageParam: page => page.has_more ? page.next_cursor : undefined,
  });

  const removeMutation = useMutation({
    mutationFn: (item: FollowItem) => removeFollow(item.target_type, item.target_id),
    onSuccess: () => {
      toast.success('已取消关注');
      queryClient.invalidateQueries({ queryKey: ['follows'] });
      queryClient.invalidateQueries({ queryKey: ['my-follows'] });
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  return (
    <div className="space-y-6">
      {query.isLoading ? (
        <SkeletonList count={3} />
      ) : query.error ? (
        <Notice
          tone="warning"
          title="关注列表暂时无法加载"
          action={<Button variant="secondary" onClick={() => query.refetch()}>重试</Button>}
        >
          {describeError(query.error).message}
        </Notice>
      ) : query.data && query.data.pages.flatMap(page => page.items).length > 0 ? (
        <><ul className="space-y-3">
          {query.data.pages.flatMap(page => page.items).map((item) => {
            const courseName =
              item.target_type === 'course'
                ? ((item.target?.name as string | undefined) ?? null)
                : null;
            const creatorName =
              item.target_type === 'creator'
                ? ((item.target?.display_name as string | undefined) ?? null)
                : null;
            return (
              <li key={item.id} className="border border-divider bg-surface p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-medium text-text-primary">
                      {item.target_type === 'course' ? '课程' : '创作者'}：
                      {item.target ? (
                        item.target_type === 'course' ? (
                          <Link
                            href={`/courses/${item.target_id}`}
                            className="underline underline-offset-2 hover:text-text-primary"
                          >
                            {courseName ?? `课程 ${item.target_id.slice(0, 8)}…`}
                          </Link>
                        ) : (
                          <Link
                            href={`/creators/${item.target_id}`}
                            className="underline underline-offset-2 hover:text-text-primary"
                          >
                            {creatorName ?? `用户 ${item.target_id.slice(0, 8)}…`}
                          </Link>
                        )
                      ) : (
                        <span className="text-text-secondary">
                          目标已不存在（可能已合并或注销），可取消关注
                        </span>
                      )}
                    </p>
                    <p className="mt-1 text-xs text-text-secondary">
                      关注于 {formatDateTime(item.created_at)}
                    </p>
                  </div>
                  <Button
                    variant="secondary"
                    isLoading={removeMutation.isPending && removeMutation.variables?.id === item.id}
                    onClick={() => removeMutation.mutate(item)}
                  >
                    取消关注
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>{query.hasNextPage && <Button onClick={()=>query.fetchNextPage()} isLoading={query.isFetchingNextPage}>更多关注</Button>}</>
      ) : (
        <EmptyState
          title="还没有关注任何课程或创作者"
          description="在课程页或贡献者主页可关注。"
        />
      )}
    </div>
  );
}
