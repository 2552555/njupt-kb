import { api } from './client';
import type { CursorPage, NotificationType } from './types';

export interface NotificationItem {
  id: string;
  type: NotificationType | string;
  title: string;
  body: string;
  data: Record<string, unknown> | null;
  is_read: boolean;
  read_at: string | null;
  created_at: string;
}

export async function listNotifications(
  cursor?: string | null,
): Promise<CursorPage<NotificationItem>> {
  const { data } = await api.get<CursorPage<NotificationItem>>('/notifications', {
    params: { cursor: cursor || undefined },
  });
  return data;
}

/** 设置单条消息已读。使用 PATCH 语义（不做 toggle）。 */
export async function setNotificationRead(
  notificationId: string,
  read: boolean,
): Promise<void> {
  await api.patch(`/notifications/${notificationId}`, { read });
}

export async function markAllNotificationsRead(): Promise<{updated:number}> {
  return (await api.post('/notifications/read-all')).data;
}

export interface UnreadCount {
  unread: number;
}

/** 未读数：SSE 不可用时作为轮询兜底（beta16 §33）。 */
export async function fetchUnreadCount(): Promise<UnreadCount> {
  const { data } = await api.get<UnreadCount>('/notifications/unread-count');
  return data;
}
