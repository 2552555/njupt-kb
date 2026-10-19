'use client';

import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { API_BASE_URL } from '@/lib/api/client';

export type NotificationStreamState = 'idle' | 'live' | 'fallback';

/**
 * 通知未读数实时流（beta16 §33）。
 *
 * - `unread` 事件只更新 `['unread-count', userId]` 缓存，不触发整页刷新或入场动画；
 * - `ping` 仅保活，不产生任何 UI 更新；
 * - 503（开关关闭）、409（连接超限）与网络错误一律静默降级为轮询：
 *   不 toast、不抛错、不无限重连，由调用方按 `fallback` 状态启用轮询；
 * - 页面隐藏时断开、可见时重连，避免后台标签页长期占用服务端连接；
 * - 组件卸载时关闭连接。
 */
export function useNotificationStream(userId: string | undefined): NotificationStreamState {
  const client = useQueryClient();
  // 不支持 EventSource（或服务端渲染）时直接进入轮询，不在 effect 里同步改状态。
  const [state, setState] = useState<NotificationStreamState>(() =>
    typeof window === 'undefined' || typeof EventSource === 'undefined' ? 'fallback' : 'idle',
  );

  useEffect(() => {
    if (!userId) return;

    let source: EventSource | null = null;
    let degraded = false;
    let closed = false;

    const disconnect = () => {
      source?.close();
      source = null;
    };

    const connect = () => {
      if (closed || degraded || document.visibilityState === 'hidden') return;

      const stream = new EventSource(`${API_BASE_URL}/notifications/stream`, {
        withCredentials: true,
      });
      source = stream;

      stream.addEventListener('open', () => {
        if (!closed) setState('live');
      });

      stream.addEventListener('unread', (event) => {
        const raw: unknown = event.data;
        if (typeof raw !== 'string') return;
        try {
          const payload: unknown = JSON.parse(raw);
          if (typeof payload !== 'object' || payload === null || !('unread' in payload)) return;
          const unread = (payload as { unread?: unknown }).unread;
          if (typeof unread === 'number' && Number.isFinite(unread)) {
            client.setQueryData(['unread-count', userId], { unread });
          }
        } catch {
          // 单帧解析失败不改变连接状态：等待下一帧，或由轮询兜底。
        }
      });

      // 保活心跳：只维持连接，不更新任何界面。
      stream.addEventListener('ping', () => {});

      stream.addEventListener('error', () => {
        if (closed) return;
        // 503/409 与网络错误都会走到这里：静默降级为轮询。
        degraded = true;
        setState('fallback');
        disconnect();
      });
    };

    const onVisibilityChange = () => {
      if (document.visibilityState === 'hidden') {
        disconnect();
        return;
      }
      if (!degraded) connect();
    };

    connect();
    document.addEventListener('visibilitychange', onVisibilityChange);

    return () => {
      closed = true;
      document.removeEventListener('visibilitychange', onVisibilityChange);
      disconnect();
    };
  }, [userId, client]);

  return state;
}
