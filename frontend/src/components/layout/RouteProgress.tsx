'use client';

import { useEffect, useRef, useState } from 'react';
import { usePathname } from 'next/navigation';

/**
 * 路由切换进度条：点击站内链接立即给出反馈，路径变化后收尾。
 * 只覆盖导航等待期，不做整页重播动画（DESIGN.md §13：动效解释状态）。
 */
export function RouteProgress() {
  const pathname = usePathname();
  const [active, setActive] = useState(false);
  const startedAt = useRef(0);

  useEffect(() => {
    function onClick(event: MouseEvent) {
      if (
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      ) {
        return;
      }
      const anchor = (event.target as HTMLElement | null)?.closest?.('a');
      if (!anchor) return;
      const href = anchor.getAttribute('href');
      if (!href || anchor.target === '_blank' || anchor.hasAttribute('download')) return;
      if (href.startsWith('#') || href.startsWith('mailto:') || href.startsWith('tel:')) return;
      const url = new URL(anchor.href, window.location.href);
      if (url.origin !== window.location.origin) return;
      if (url.pathname === window.location.pathname && url.search === window.location.search) return;
      startedAt.current = Date.now();
      setActive(true);
    }

    document.addEventListener('click', onClick, true);
    return () => document.removeEventListener('click', onClick, true);
  }, []);

  useEffect(() => {
    if (!active) return;
    // 最短展示 240ms：闪烁一下就消失的进度条只会造成干扰。
    const wait = Math.max(0, 240 - (Date.now() - startedAt.current));
    const timer = window.setTimeout(() => setActive(false), wait);
    return () => window.clearTimeout(timer);
  }, [active, pathname]);

  if (!active) return null;

  return (
    <div
      role="progressbar"
      aria-label="页面加载中"
      aria-busy="true"
      className="dn-progress-bar fixed inset-x-0 top-0 z-50 h-0.5 bg-brand"
    />
  );
}
