/**
 * 统一 HTTP 客户端（docs/ARCHITECTURE.md §7 前端契约）。
 *
 * - 会话走 HttpOnly Cookie，浏览器自动携带；前端不存 token（不再使用 localStorage）。
 * - 修改类请求注入 CSRF 双提交令牌 `X-CSRF-Token`，值来自非 HttpOnly 的 `njupt_csrf` Cookie。
 * - 响应统一解析错误体 `{ error: { code, message, request_id, details } }`。
 * - 401 跳登录（初始化探测 `/me` 时静默处理）。
 */
import axios, {
  AxiosError,
  AxiosInstance,
  InternalAxiosRequestConfig,
} from 'axios';

import type { ApiErrorBody } from './types';

export const API_BASE_URL = '/api/v1';
export const CSRF_COOKIE_NAME = 'njupt_csrf';
export const CSRF_HEADER_NAME = 'X-CSRF-Token';

const UNSAFE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

declare module 'axios' {
  export interface AxiosRequestConfig {
    /** 401 时不自动跳登录（用于应用启动时的会话探测）。 */
    skipAuthRedirect?: boolean;
  }
}

/** 归一化后的接口错误，页面据此给出「发生了什么、接下来怎么做」。 */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly requestId: string;
  readonly details: Record<string, unknown> | null;

  constructor(params: {
    code: string;
    message: string;
    status: number;
    requestId?: string;
    details?: Record<string, unknown> | null;
  }) {
    super(params.message);
    this.name = 'ApiError';
    this.code = params.code;
    this.status = params.status;
    this.requestId = params.requestId ?? '';
    this.details = params.details ?? null;
  }
}

/** 从错误对象取出可展示文案；非 ApiError 走通用回退。 */
export function describeError(error: unknown): {
  code: string;
  message: string;
  requestId: string;
} {
  if (error instanceof ApiError) {
    return { code: error.code, message: error.message, requestId: error.requestId };
  }
  if (error instanceof Error) {
    return { code: 'CLIENT_ERROR', message: error.message, requestId: '' };
  }
  return { code: 'UNKNOWN_ERROR', message: '请求未完成，请稍后重试', requestId: '' };
}

function readCookie(name: string): string | null {
  if (typeof document === 'undefined') return null;
  const match = document.cookie.match(
    new RegExp(`(?:^|; )${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}=([^;]*)`),
  );
  return match ? decodeURIComponent(match[1]) : null;
}

let csrfToken: string | null = null;

export function getCsrfToken(): string | null {
  return csrfToken ?? readCookie(CSRF_COOKIE_NAME);
}

/** 拉取 CSRF 令牌并缓存到内存（同时后端会写入 Cookie）。 */
export async function ensureCsrfToken(): Promise<string | null> {
  const existing = getCsrfToken();
  if (existing) return existing;
  try {
    const response = await api.get<{ csrf_token: string }>('/auth/csrf', {
      skipAuthRedirect: true,
    });
    csrfToken = response.data.csrf_token ?? readCookie(CSRF_COOKIE_NAME);
    return csrfToken;
  } catch {
    return null;
  }
}

/** 会话失效时的默认处理，可在应用层覆盖。 */
let onUnauthorized: () => void = () => {
  if (typeof window === 'undefined') return;
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  if (window.location.pathname !== '/login') {
    window.location.assign(`/login?next=${next}`);
  }
};

export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler;
}

export const api: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' },
});

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const method = (config.method ?? 'get').toUpperCase();
  if (UNSAFE_METHODS.has(method)) {
    const token = getCsrfToken();
    if (token) {
      config.headers.set(CSRF_HEADER_NAME, token);
    }
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ApiErrorBody>) => {
    const status = error.response?.status ?? 0;
    const body = error.response?.data;
    const code = body?.error?.code ?? `HTTP_${status || 'NETWORK'}`;
    const message =
      body?.error?.message ??
      (status === 0
        ? '无法连接服务器，请检查网络后重试'
        : '请求未完成，请稍后重试');

    // CSRF 失效时刷新令牌重试一次（Cookie 可能刚被轮换或首次进入）。
    if (
      status === 403 &&
      code === 'CSRF_FAILED' &&
      error.config &&
      !(error.config as InternalAxiosRequestConfig & { _csrfRetried?: boolean })._csrfRetried
    ) {
      const retried = error.config as InternalAxiosRequestConfig & {
        _csrfRetried?: boolean;
      };
      retried._csrfRetried = true;
      csrfToken = null;
      const token = await ensureCsrfToken();
      if (token) {
        retried.headers.set(CSRF_HEADER_NAME, token);
        return api.request(retried);
      }
    }

    if (status === 401 && !error.config?.skipAuthRedirect) {
      onUnauthorized();
    }

    return Promise.reject(
      new ApiError({
        code,
        message,
        status,
        requestId: body?.error?.request_id,
        details: body?.error?.details ?? null,
      }),
    );
  },
);
