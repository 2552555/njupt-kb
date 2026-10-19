'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import {
  Award,
  Bell,
  BookOpen,
  ChevronDown,
  FileUp,
  Gavel,
  Heart,
  Home,
  LogOut,
  ScrollText,
  Search,
  MessageSquare,
  ShieldCheck,
  UserCog,
  UserPlus,
} from 'lucide-react';

import { Button } from '@/components/ui/Button';
import { Avatar } from '@/components/common/Avatar';
import { NotificationBell } from '@/components/layout/NotificationBell';
import { canAccessAdmin, useAuthStore } from '@/stores/authStore';

const mainNav = [
  { href: '/', label: '首页', icon: Home },
  { href: '/courses', label: '课程', icon: BookOpen },
  { href: '/search', label: '搜索', icon: Search },
  { href: '/requests', label: '求资料', icon: MessageSquare },
];

function isActive(pathname: string, href: string): boolean {
  if (href === '/') return pathname === '/';
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function AppHeader() {
  const pathname = usePathname();
  const router = useRouter();
  const { user, status, logout } = useAuthStore();
  const [menuPath, setMenuPath] = useState<string | null>(null);
  const menuOpen = menuPath === pathname;
  const setMenuOpen = (open: boolean) => setMenuPath(open ? pathname : null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDocClick(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuPath(null);
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
        <Link href="/" className="flex shrink-0 items-center gap-2" aria-label="南邮课程知识平台 · 返回首页">
          {/* 品牌标识用排版而非图标：校方徽标未获授权前不虚构校徽（DESIGN.md §3）。 */}
          <span className="flex flex-col leading-none">
            <span className="text-[0.5625rem] font-semibold tracking-[0.22em] text-text-secondary">
              南京邮电大学
            </span>
            <span className="dn-display mt-1 text-base text-text-primary sm:text-lg">
              课程知识平台
            </span>
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
          <Link
            href="/upload"
            className="hidden min-h-11 items-center gap-2 bg-brand px-3 text-sm font-medium text-on-color hover:bg-brand/90 lg:inline-flex"
          >
            <FileUp className="h-4 w-4" aria-hidden="true" />
            上传资料
          </Link>
          {authed ? (
            <>
              <NotificationBell />
              <div className="relative" ref={menuRef}>
                <button
                  type="button"
                  onClick={() => setMenuOpen(!menuOpen)}
                  aria-expanded={menuOpen}
                  aria-haspopup="menu"
                  className="flex min-h-11 items-center gap-2 px-2 text-sm text-text-primary hover:bg-canvas"
                >
                  <Avatar url={user.avatar_url} name={user.display_name} />
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
                    <MenuLink href="/me" icon={UserCog} label="个人总览" />
                    <MenuLink href="/me/resources" icon={BookOpen} label="我的资料" />
                    <MenuLink href="/me/contributions" icon={Award} label="我的贡献" />
                    <MenuLink href="/me/guides" icon={BookOpen} label="我的攻略" />
                    <MenuLink href="/me/contributor-application" icon={UserPlus} label="入驻申请" />
                    <MenuLink href="/me/cases" icon={Gavel} label="我的案件" />
                    <MenuLink href="/me/agreements" icon={ScrollText} label="我的协议" />
                    <MenuLink href="/me/profile" icon={UserCog} label="个人资料" />
                    <MenuLink href="/me/favorites" icon={Heart} label="我的收藏" />
                    <MenuLink href="/me/follows" icon={BookOpen} label="我的关注" />
                    <MenuLink href="/me/notifications" icon={Bell} label="通知" />
                    <MenuLink href="/me/security" icon={UserCog} label="账号与安全" />
                    {canAccessAdmin(user) && (
                      <MenuLink href="/admin" icon={ShieldCheck} label="管理后台" />
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
                const next = pathname === '/login' ? '/' : pathname + window.location.search;
                router.push(`/login?next=${encodeURIComponent(next)}`);
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
