/**
 * 服务端数据获取（公共课程页尽量 SSR，设计文档 §8、§19.3）。
 *
 * 直接把浏览器 Cookie 转发给后端，使服务端渲染与用户登录态一致；
 * 不缓存私有响应（`cache: 'no-store'`），公开页面由页面级 revalidate 控制。
 *
 * 返回结构化结果而不是抛异常：页面据此给出明确静态回退，
 * 不把接口故障显示成「暂无资料」（§39 A21）。
 */
import { cookies } from 'next/headers';

const API_ORIGIN =
  process.env.API_INTERNAL_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  'http://localhost:8000';

export const SERVER_API_BASE = `${API_ORIGIN}/api/v1`;

export interface ServerError {
  code: string;
  message: string;
  status: number;
}

export interface ServerResult<T> {
  data: T | null;
  error: ServerError | null;
}

export async function serverFetch<T>(path: string): Promise<ServerResult<T>> {
  let cookieHeader = '';
  try {
    const store = await cookies();
    cookieHeader = store
      .getAll()
      .map((item) => `${item.name}=${item.value}`)
      .join('; ');
  } catch {
    cookieHeader = '';
  }

  try {
    // 不缓存：可见性以数据库为事实源，避免把下架或私有内容缓存下来（§19.2）。
    const response = await fetch(`${SERVER_API_BASE}${path}`, {
      headers: cookieHeader ? { cookie: cookieHeader } : undefined,
      cache: 'no-store',
    });

    if (!response.ok) {
      let code = `HTTP_${response.status}`;
      let message = '暂时无法获取数据，请稍后重试';
      try {
        const body = (await response.json()) as {
          error?: { code?: string; message?: string };
        };
        code = body.error?.code ?? code;
        message = body.error?.message ?? message;
      } catch {
        /* 响应体不是 JSON 时保留默认文案 */
      }
      return { data: null, error: { code, message, status: response.status } };
    }

    const data = (await response.json()) as T;
    return { data, error: null };
  } catch {
    return {
      data: null,
      error: { code: 'NETWORK_ERROR', message: '无法连接服务器，请稍后重试', status: 0 },
    };
  }
}
