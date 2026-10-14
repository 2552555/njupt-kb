'use client';

import Link from 'next/link';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { SkeletonList } from '@/components/ui/Skeleton';
import { describeError } from '@/lib/api/client';
import { listFavorites, removeFavorite } from '@/lib/api/interaction';
import { formatDateTime } from '@/lib/utils';

const unavailableReasons: Record<string, string> = {
  withdrawn: '该资料已被作者或管理员下架，不再提供新的预览或下载授权。',
  archived: '该资料已归档，不再作为公开内容提供。',
  unpublished: '该资料当前不是已发布状态，可能正在审核或等待处理。',
};

export default function MyFavoritesPage() {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ['favorites'],
    queryFn: () => listFavorites(),
  });

  const removeMutation = useMutation({
    mutationFn: (resourceId: string) => removeFavorite(resourceId),
    onSuccess: () => {
      toast.success('已取消收藏');
      queryClient.invalidateQueries({ queryKey: ['favorites'] });
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  return (
    <div className="space-y-6">
      <Notice tone="info" title="收藏作用于资源本身">
        公开版本变更后收藏仍然保留；资料下架时显示不可用状态，不会自动打开旧版本文件。
      </Notice>

      {query.isLoading ? (
        <SkeletonList count={3} />
      ) : query.error ? (
        <Notice
          tone="warning"
          title="收藏列表暂时无法加载"
          action={<Button variant="secondary" onClick={() => query.refetch()}>重试</Button>}
        >
          {describeError(query.error).message}。这不表示你没有收藏。
        </Notice>
      ) : query.data && query.data.items.length > 0 ? (
        <ul className="space-y-3">
          {query.data.items.map((item) => (
            <li key={item.resource_id} className="border border-divider bg-surface p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  {item.available ? (
                    <Link
                      href={`/resources/${item.resource_id}`}
                      className="font-medium text-text-primary underline underline-offset-2"
                    >
                      {item.title ?? '（未命名资料）'}
                    </Link>
                  ) : (
                    <span className="font-medium text-text-secondary">
                      {item.title ?? '（未命名资料）'}
                    </span>
                  )}
                  <p className="mt-1 text-xs text-text-secondary">
                    收藏于 {formatDateTime(item.created_at)}
                  </p>
                  {!item.available && (
                    <p className="mt-2 max-w-2xl text-sm text-text-primary">
                      <span className="font-medium">不可用原因：</span>
                      {unavailableReasons[item.publication_status] ??
                        '该资料当前不可用（可能已下架或未发布）。'}
                      {' '}这是内容状态变化，不是网络故障。
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 gap-2">
                  {item.available ? (
                    <Link
                      href={`/resources/${item.resource_id}`}
                      className="inline-flex min-h-11 items-center bg-brand px-4 text-sm font-medium text-on-color hover:bg-brand/90"
                    >
                      返回资料
                    </Link>
                  ) : (
                    <Link
                      href={`/resources/${item.resource_id}`}
                      className="inline-flex min-h-11 items-center border border-control-border px-4 text-sm font-medium text-text-primary hover:bg-canvas"
                    >
                      查看说明
                    </Link>
                  )}
                  <Button
                    variant="ghost"
                    onClick={() => removeMutation.mutate(item.resource_id)}
                    isLoading={
                      removeMutation.isPending && removeMutation.variables === item.resource_id
                    }
                  >
                    取消收藏
                  </Button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <Notice tone="info" title="还没有收藏">
          在资料详情页可以收藏资料；收藏后这里会显示可用状态与下架提示。
        </Notice>
      )}
    </div>
  );
}
