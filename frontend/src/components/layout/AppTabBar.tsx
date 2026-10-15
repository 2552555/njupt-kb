'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { BookOpen, Home, Search, User } from 'lucide-react';

const items = [
  { href: '/', label: '首页', icon: Home },
  { href: '/courses', label: '课程', icon: BookOpen },
  { href: '/search', label: '搜索', icon: Search },
  { href: '/me/resources', label: '我的', icon: User },
];

function isActive(pathname: string, href: string): boolean {
  if (href === '/') return pathname === '/';
  return pathname === href || pathname.startsWith(`${href}/`);
}

/** 移动端底部导航：核心任务（搜索、课程、本人内容）在拇指区可达（§39 A20）。 */
export function AppTabBar() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="移动端导航"
      className="fixed inset-x-0 bottom-0 z-20 border-t border-divider bg-surface md:hidden"
    >
      <ul className="mx-auto flex max-w-6xl">
        {items.map((item) => {
          const active = isActive(pathname, item.href);
          const Icon = item.icon;
          return (
            <li key={item.href} className="flex-1">
              <Link
                href={item.href}
                aria-current={active ? 'page' : undefined}
                className={`flex min-h-14 flex-col items-center justify-center gap-0.5 text-xs font-medium ${
                  active ? 'text-text-primary' : 'text-text-secondary'
                }`}
              >
                <span
                  className={`flex h-7 w-12 items-center justify-center ${
                    active ? 'bg-brand text-on-color' : ''
                  }`}
                >
                  <Icon className="h-5 w-5" aria-hidden="true" />
                </span>
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
