'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { ReactNode, useEffect } from 'react';
import { Lock, ShieldAlert } from 'lucide-react';

import { canReview, useAuthStore } from '@/stores/authStore';

function StatusBlock({
  title,
  description,
  icon: Icon,
}: {
  title: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
}) {
  return (
    <div className="mx-auto max-w-lg border border-divider bg-surface p-8 text-center">
      <div className="mx-auto mb-4 grid h-14 w-14 place-items-center bg-steel text-on-color">
        <Icon className="h-7 w-7" aria-hidden="true" />
      </div>
      <h2 className="text-lg font-semibold text-text-primary">{title}</h2>
      <p className="mt-2 text-sm text-text-secondary">{description}</p>
      <Link
        href="/"
        className="mt-6 inline-flex min-h-11 items-center bg-brand px-4 text-sm font-medium text-on-color hover:bg-brand/90"
      >
        返回首页
      </Link>
    </div>
  );
}

/**
 * 前端访问门禁只用于界面引导；真正的权限判定始终在服务端（§5.5）。
 * 未登录时跳转登录页并带上回跳地址。
 */
export function AuthGuard({
  children,
  requireReview = false,
}: {
  children: ReactNode;
  requireReview?: boolean;
}) {
  const { user, status } = useAuthStore();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (status === 'guest') {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    }
  }, [status, router, pathname]);

  if (status !== 'authenticated' || !user) {
    return (
      <div className="py-10" role="status" aria-live="polite">
        <div className="mx-auto max-w-lg border border-divider bg-surface p-8 text-center">
          <div className="mx-auto mb-4 grid h-14 w-14 place-items-center bg-steel text-on-color">
            <Lock className="h-7 w-7" aria-hidden="true" />
          </div>
          <p className="text-sm text-text-secondary">正在确认登录状态…</p>
        </div>
      </div>
    );
  }

  if (requireReview && !canReview(user)) {
    return (
      <StatusBlock
        icon={ShieldAlert}
        title="需要审核权限"
        description="该页面只对审核员与管理员开放。如果你的职责包含审核，请联系管理员调整账号角色。"
      />
    );
  }

  return <>{children}</>;
}
