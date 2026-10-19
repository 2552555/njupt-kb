'use client';

import { useEffect } from 'react';
import Link from 'next/link';

import { Button } from '@/components/ui/Button';

/**
 * 页面级错误兜底：与 not-found 区分——这里表示页面加载或渲染失败，
 * 不是内容不存在。按 §26.2 说明「发生了什么、接下来怎么做」，并提供重试。
 * 渲染在 (app) 布局内，顶栏与页脚保留，用户可直接导航离开。
 */
export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // 原始错误只进控制台，不在界面上暴露堆栈（§27）。
    console.error(error);
  }, [error]);

  return (
    <div className="mx-auto flex min-h-[60vh] max-w-2xl flex-col items-center justify-center px-4 py-16 text-center">
      <p className="dn-kicker text-text-secondary">SOMETHING WENT WRONG</p>
      <h1 className="dn-display mt-2 text-4xl">这一页出错了</h1>
      <p className="mt-4 text-text-secondary">
        发生了什么：页面在加载或渲染时遇到问题，不是内容本身不存在，你已提交的数据不会因此丢失。
      </p>
      <p className="mt-2 text-text-secondary">
        怎么办：可以点击「重试」重新加载这一页；如果反复出现，请稍后再试，或先从上方导航前往其他页面。
      </p>
      <div className="mt-8 flex flex-wrap justify-center gap-3">
        <Button variant="primary" onClick={() => reset()}>
          重试
        </Button>
        <Link
          href="/"
          className="inline-flex min-h-11 items-center border border-control-border px-4 text-sm font-medium text-text-primary hover:bg-canvas"
        >
          返回首页
        </Link>
      </div>
      {error.digest && (
        <p className="mt-8 text-sm text-text-secondary">错误编号：{error.digest}</p>
      )}
    </div>
  );
}
