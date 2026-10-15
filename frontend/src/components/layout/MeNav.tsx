'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const items = [
  { href: '/me/resources', label: '我的资料' },
  { href: '/me/favorites', label: '我的收藏' },
  { href: '/me/notifications', label: '通知' },
  { href: '/me/security', label: '账号与安全' },
];

export function MeNav() {
  const pathname = usePathname();

  return (
    <nav aria-label="个人中心导航" className="border-b border-divider">
      <ul className="flex flex-wrap gap-1">
        {items.map((item) => {
          const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
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
