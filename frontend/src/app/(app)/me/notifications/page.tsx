'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { SkeletonList } from '@/components/ui/Skeleton';
import { describeError } from '@/lib/api/client';
import { listNotifications, setNotificationRead } from '@/lib/api/notification';
import { formatDateTime } from '@/lib/utils';

export default function NotificationsPage() {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ['notifications'],
    queryFn: () => listNotifications(),
  });

  const readMutation = useMutation({
    mutationFn: (id: string) => setNotificationRead(id, true),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notifications'] });
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const items = query.data?.items ?? [];
  const unreadCount = items.filter((item) => !item.read).length;

  return (
    <div className="space-y-6">
      {query.isLoading ? (
        <SkeletonList count={3} />
      ) : query.error ? (
        <Notice
          tone="warning"
          title="通知暂时无法加载"
          action={<Button variant="secondary" onClick={() => query.refetch()}>重试</Button>}
        >
          {describeError(query.error).message}。通知服务可能尚未在当前环境启用，
          这里不会显示编造的未读数或消息。
        </Notice>
      ) : items.length > 0 ? (
        <>
          <p className="text-sm text-text-secondary">
            共 {items.length} 条通知，其中 {unreadCount} 条未读
          </p>
          <ul className="space-y-3">
            {items.map((item) => (
              <li
                key={item.id}
                className={`border border-divider bg-surface p-4 ${
                  item.read ? '' : 'border-l-4 border-l-brand'
                }`}
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-medium text-text-primary">{item.title}</p>
                    <p className="mt-1 text-sm text-text-secondary">{item.body}</p>
                    <p className="mt-1 text-xs text-text-secondary">
                      {formatDateTime(item.created_at)}
                      {item.read ? '' : ' · 未读'}
                    </p>
                  </div>
                  {!item.read && (
                    <Button
                      variant="ghost"
                      onClick={() => readMutation.mutate(item.id)}
                      isLoading={readMutation.isPending && readMutation.variables === item.id}
                    >
                      标记已读
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <Notice tone="info" title="还没有通知">
          提交受理、审核结果、版本发布与资源撤下等事件会在这里通知你。
        </Notice>
      )}
    </div>
  );
}
