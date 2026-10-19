'use client';

import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { Bell } from 'lucide-react';

import { fetchUnreadCount } from '@/lib/api/notification';
import { useNotificationStream } from '@/lib/hooks/useNotificationStream';
import { useAuthStore } from '@/stores/authStore';

/**
 * 通知入口：未读数由 SSE 推送更新；流不可用时回退为 60 秒轮询。
 * 只更新未读数字，不重播入场动画，也不打扰用户。
 */
export function NotificationBell() {
  const user = useAuthStore((s) => s.user);
  const stream = useNotificationStream(user?.id);
  const query = useQuery({
    queryKey: ['unread-count', user?.id],
    queryFn: fetchUnreadCount,
    enabled: !!user,
    refetchInterval: stream === 'live' ? false : 60_000,
  });

  const unread = query.data?.unread ?? 0;

  return (
    <Link
      href="/me/notifications"
      aria-label={unread > 0 ? `通知，${unread} 条未读` : '通知'}
      className="relative flex min-h-11 min-w-11 items-center justify-center text-text-secondary hover:bg-canvas hover:text-text-primary"
    >
      <Bell className="h-5 w-5" aria-hidden="true" />
      {unread > 0 && (
        <span
          aria-hidden="true"
          className="absolute right-1 top-1 min-w-4 bg-brand px-1 text-center text-[0.625rem] font-semibold leading-4 text-on-color"
        >
          {unread > 99 ? '99+' : unread}
        </span>
      )}
    </Link>
  );
}
