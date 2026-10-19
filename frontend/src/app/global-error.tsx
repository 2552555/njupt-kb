'use client';

import { useEffect } from 'react';

import { Button } from '@/components/ui/Button';

/**
 * 全局错误兜底：在根布局本身失败时替换整页（必须自带 html/body）。
 * 内容同样说明「发生了什么、接下来怎么做」并提供重试（§26.2）；
 * 不引入布局组件，避免在布局已失败的场景叠加依赖。
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <html lang="zh-CN">
      <body className="min-h-full bg-canvas font-sans text-text-primary antialiased">
        <div className="mx-auto flex min-h-screen max-w-2xl flex-col items-center justify-center px-4 py-16 text-center">
          <p className="dn-kicker text-text-secondary">SOMETHING WENT WRONG</p>
          <h1 className="dn-display mt-2 text-4xl">平台暂时出错了</h1>
          <p className="mt-4 text-text-secondary">
            发生了什么：页面框架加载失败，可能是网络波动或服务暂时不可用，不是内容不存在。
          </p>
          <p className="mt-2 text-text-secondary">
            怎么办：点击「重试」重新加载；如果反复出现，请稍后再访问。
          </p>
          <div className="mt-8">
            <Button variant="primary" onClick={() => reset()}>
              重试
            </Button>
          </div>
          {error.digest && (
            <p className="mt-8 text-sm text-text-secondary">错误编号：{error.digest}</p>
          )}
        </div>
      </body>
    </html>
  );
}
