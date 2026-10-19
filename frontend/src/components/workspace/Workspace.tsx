'use client';
import { ReactNode } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { SkeletonList } from '@/components/ui/Skeleton';
import { describeError } from '@/lib/api/client';
import { getWorkspacePage } from '@/lib/api/workspaces';
import { hasPermission, isAdmin, useAuthStore } from '@/stores/authStore';

export const fieldClass = 'min-h-11 w-full border border-control-border bg-surface px-3 py-2 text-sm';
export function useWorkspaceList<T>(key: string, path: string, params: Record<string, string> = {}) {
  const user = useAuthStore(s => s.user);
  return useInfiniteQuery({
    queryKey: [key, user?.id, params], initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => getWorkspacePage<T>(path, params, pageParam),
    getNextPageParam: page => page.has_more ? page.next_cursor : undefined,
    enabled: !!user,
  });
}
export function QueryFeedback({ pending, error, empty, retry, emptyText = '暂无记录' }: {
  pending: boolean; error: unknown; empty: boolean; retry: () => void; emptyText?: string;
}) {
  if (error) return <Notice tone="error" title="加载失败" action={<Button onClick={retry}>重试</Button>}>{describeError(error).message}</Notice>;
  if (pending) return <SkeletonList count={3} />;
  return empty ? <Notice title={emptyText} /> : null;
}
export function ManagementGuard({ children, reviewer = false, permission }: { children: ReactNode; reviewer?: boolean; permission?: string }) {
  const user = useAuthStore(s => s.user);
  if (permission ? !hasPermission(user, permission) : !isAdmin(user) && !(reviewer && user?.role === 'reviewer')) return <Notice title="需要管理权限" />;
  return <>{children}</>;
}
