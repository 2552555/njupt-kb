'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const items = [
  { href: '/me', label: '总览' },
  { href: '/me/feed', label: '关注动态' },
  { href: '/me/downloads', label: '下载历史' },
  { href: '/me/profile', label: '个人资料' },
  { href: '/me/resources', label: '我的资料' },
  { href: '/me/contributions', label: '我的贡献' },
  { href: '/me/guides', label: '我的攻略' },
  { href: '/me/favorites', label: '我的收藏' },
  { href: '/me/follows', label: '我的关注' },
  { href: '/me/notifications', label: '通知' },
  { href: '/me/reports', label: '举报与申诉' },
  { href: '/me/contributor-application', label: '入驻申请' },
  { href: '/me/cases', label: '我的案件' },
  { href: '/me/agreements', label: '我的协议' },
  { href: '/me/settlements', label: '结算单' },
  { href: '/me/security', label: '账号与安全' },
];

export function MeNav() {
  const pathname = usePathname();

  return (
    <nav aria-label="个人中心导航" className="border-b border-divider">
      <ul className="flex flex-wrap gap-1">
        {items.map((item) => {
          const active = pathname === item.href || (item.href !== '/me' && pathname.startsWith(`${item.href}/`));
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                aria-current={active ? 'page' : undefined}
                className={`flex min-h-11 items-center px-4 text-sm font-medium ${
                  active
                    ? 'bg-brand text-on-color'
                    : 'text-text-secondary hover:bg-canvas hover:text-text-primary'
                }`}
              >
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
