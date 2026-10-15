import Link from 'next/link';

import { AppHeader } from '@/components/layout/AppHeader';
import { AppTabBar } from '@/components/layout/AppTabBar';

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <AppHeader />
      <main
        id="main-content"
        className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 pb-24 md:pb-10"
      >
        {children}
      </main>
      <footer className="border-t border-divider bg-surface">
        <div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 py-8 text-sm text-text-secondary md:flex-row md:items-center md:justify-between">
          <p>南邮课程知识平台 · 以课程组织可追溯的学习内容</p>
          <nav aria-label="页脚导航" className="flex flex-wrap gap-x-4 gap-y-2">
            <Link href="/courses" className="underline underline-offset-2 hover:text-text-primary">
              课程
            </Link>
            <Link href="/search" className="underline underline-offset-2 hover:text-text-primary">
              搜索
            </Link>
            <Link href="/me/security" className="underline underline-offset-2 hover:text-text-primary">
              账号与安全
            </Link>
          </nav>
        </div>
      </footer>
      <AppTabBar />
    </div>
  );
}
