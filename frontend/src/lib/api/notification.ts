/**
 * 站内通知接口（设计文档 §21、§25.2）。
 *
 * 注意：后端 notification 模块尚未在本分支落地（main.py 已预留 include_router）。
 * 本客户端按 §25.2 的 `GET /notifications`、`PATCH /notifications/{id}` 契约编写，
 * 字段以最小必要集为准；后端 DTO 就绪后需核对 `NotificationItem`。
 * 页面在接口不可用时必须显示明确不可用状态，不编造未读数。
 */
import { api } from './client';
import type { CursorPage, NotificationType } from './types';

export interface NotificationItem {
  id: string;
  type: NotificationType | string;
  title: string;
  body: string;
  data: Record<string, unknown> | null;
  read: boolean;
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
