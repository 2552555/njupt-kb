/**
 * 会话与 UI 状态（Zustand）。
 *
 * 只保存会话与界面状态，不重复存放服务端列表/详情数据（设计文档 §8）：
 * 列表与详情一律走 TanStack Query。
 * 不保存任何令牌——会话由 HttpOnly Cookie 维持。
 */
import { create } from 'zustand';

import {
  fetchMe,
  login as loginApi,
  logout as logoutApi,
  type MeResponse,
} from '@/lib/api/auth';
import type { Role } from '@/lib/api/types';

export type SessionStatus = 'loading' | 'authenticated' | 'guest';

interface AuthState {
  user: MeResponse | null;
  status: SessionStatus;
  /** 启动时探测会话；未登录不视为错误。 */
  hydrate: () => Promise<void>;
  login: (email: string, password: string, otp?: string) => Promise<MeResponse>;
  logout: () => Promise<void>;
  setUser: (user: MeResponse | null) => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  status: 'loading',

  hydrate: async () => {
    const user = await fetchMe();
    set({ user, status: user ? 'authenticated' : 'guest' });
  },

  login: async (email, password, otp) => {
    const user = await loginApi({ email, password, otp });
    set({ user, status: 'authenticated' });
    return user;
  },

  logout: async () => {
    try {
      await logoutApi();
    } finally {
      set({ user: null, status: 'guest' });
    }
  },

  setUser: (user) =>
    set({ user, status: user ? 'authenticated' : 'guest' }),
}));

/** 审核与发布相关入口仅对审核员与管理员开放（§15）。 */
export function canReview(user: MeResponse | null): boolean {
  return hasPermission(user, 'resource.review');
}

export function hasPermission(user: MeResponse | null, permission: string): boolean {
  if (!user || user.status !== 'active') return false;
  if (user.permissions) return user.permissions.includes(permission);
  // 仅用于尚未刷新会话的旧页面；服务端始终重新检查权限。
  return user.role === 'admin' || (user.role === 'reviewer' && [
    'resource.review', 'report.handle', 'correction.handle', 'audit.view',
    'contributor.review', 'case.handle', 'rights_notice.handle',
  ].includes(permission));
}

export function canAccessAdmin(user: MeResponse | null): boolean {
  if (!user || user.status !== 'active') return false;
  return canReview(user) || !!user?.permissions?.some(permission => ![
    'resource.submit', 'resource.revise', 'resource.withdraw', 'ai.invoke',
  ].includes(permission));
}

export function isAdmin(user: MeResponse | null): boolean {
  return user?.role === 'admin';
}

export function hasRole(user: MeResponse | null, roles: Role[]): boolean {
  return user ? roles.includes(user.role) : false;
}
