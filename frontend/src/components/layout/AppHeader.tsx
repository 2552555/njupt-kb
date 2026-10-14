'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import {
  Bell,
  BookOpen,
  ChevronDown,
  FileUp,
  Heart,
  Home,
  LogOut,
  Search,
  ShieldCheck,
  UserCog,
} from 'lucide-react';

import { Button } from '@/components/ui/Button';
import { canReview, useAuthStore } from '@/stores/authStore';

const mainNav = [
  { href: '/', label: '首页', icon: Home },
  { href: '/courses', label: '课程', icon: BookOpen },
  { href: '/search', label: '搜索', icon: Search },
];

function isActive(pathname: string, href: string): boolean {
  if (href === '/') return pathname === '/';
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function AppHeader() {
  const pathname = usePathname();
  const { user, status, logout } = useAuthStore();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  useEffect(() => {
    function onDocClick(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    }
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, []);

  const authed = status === 'authenticated' && !!user;

  return (
    <header className="dn-acrylic sticky top-0 z-20">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-3 focus:z-30 focus:bg-ink focus:px-3 focus:py-2 focus:text-sm focus:text-on-ink"
      >
        跳到主要内容
      </a>
      <div className="mx-auto flex h-15 max-w-6xl items-center gap-4 px-4">
        <Link href="/" className="flex shrink-0 items-center gap-2">
          <span className="grid h-9 w-9 place-items-center bg-brand text-base font-semibold text-on-color">
            邮
          </span>
          <span className="dn-display hidden text-lg text-text-primary sm:block">
            南邮课程知识平台
          </span>
        </Link>

        <nav aria-label="主导航" className="ml-2 hidden items-center gap-1 md:flex">
          {mainNav.map((item) => {
            const active = isActive(pathname, item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? 'page' : undefined}
                className={`flex min-h-11 items-center gap-2 px-3 text-sm font-medium ${
                  active
                    ? 'bg-brand text-on-color'
                    : 'text-text-secondary hover:bg-canvas hover:text-text-primary'
                }`}
              >
                <Icon className="h-4 w-4" aria-hidden="true" />
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center gap-2">
          {authed ? (
            <>
              <Link
                href="/upload"
                className="hidden min-h-11 items-center gap-2 bg-brand px-4 text-sm font-medium text-on-color hover:bg-brand/90 sm:inline-flex"
              >
                <FileUp className="h-4 w-4" aria-hidden="true" />
                上传资料
              </Link>
              <div className="relative" ref={menuRef}>
                <button
                  type="button"
                  onClick={() => setMenuOpen((open) => !open)}
                  aria-expanded={menuOpen}
                  aria-haspopup="menu"
                  className="flex min-h-11 items-center gap-2 px-2 text-sm text-text-primary hover:bg-canvas"
                >
                  <span className="grid h-8 w-8 place-items-center bg-ink text-sm font-medium text-on-ink">
                    {user.display_name?.charAt(0) || 'U'}
                  </span>
                  <ChevronDown className="h-4 w-4 text-text-secondary" aria-hidden="true" />
                  <span className="sr-only">账号菜单</span>
                </button>

                {menuOpen && (
                  <div
                    role="menu"
                    className="absolute right-0 mt-1 w-60 border border-divider bg-surface py-1 shadow-3"
                  >
                    <div className="border-b border-divider px-4 py-3">
                      <p className="text-sm font-medium text-text-primary">
                        {user.display_name}
                      </p>
                      <p className="truncate text-xs text-text-secondary">{user.email}</p>
                      {!user.email_verified && (
                        <p className="mt-1 text-xs text-text-secondary">
                          邮箱未验证，部分操作受限
                        </p>
                      )}
                    </div>
                    <MenuLink href="/me/resources" icon={BookOpen} label="我的资料" />
                    <MenuLink href="/me/favorites" icon={Heart} label="我的收藏" />
                    <MenuLink href="/me/notifications" icon={Bell} label="通知" />
                    <MenuLink href="/me/security" icon={UserCog} label="账号与安全" />
                    {canReview(user) && (
                      <MenuLink href="/admin/reviews" icon={ShieldCheck} label="审核后台" />
                    )}
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setMenuOpen(false);
                        void logout();
                      }}
                      className="flex w-full min-h-11 items-center gap-2 px-4 text-sm text-text-secondary hover:bg-canvas hover:text-text-primary"
                    >
                      <LogOut className="h-4 w-4" aria-hidden="true" />
                      退出登录
                    </button>
                  </div>
                )}
              </div>
            </>
          ) : (
            <Button
              variant="primary"
              onClick={() => {
                window.location.assign('/login');
              }}
            >
              登录
            </Button>
          )}
        </div>
      </div>
    </header>
  );
}

function MenuLink({
  href,
  icon: Icon,
  label,
}: {
  href: string;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
}) {
  return (
    <Link
      href={href}
      role="menuitem"
      className="flex min-h-11 items-center gap-2 px-4 text-sm text-text-secondary hover:bg-canvas hover:text-text-primary"
    >
      <Icon className="h-4 w-4" aria-hidden="true" />
      {label}
    </Link>
  );
}
