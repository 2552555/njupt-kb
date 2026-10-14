import { AuthGuard } from '@/components/layout/AuthGuard';

export const metadata = {
  robots: { index: false, follow: false },
};

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard requireReview>
      <div className="space-y-6">
        <header>
          <p className="dn-kicker text-text-secondary">MODERATION</p>
          <h1 className="dn-display mt-1 text-3xl">审核后台</h1>
          <p className="mt-2 max-w-2xl text-sm text-text-secondary">
            审核决定针对提交时的版本快照；并发审核时只有针对当前快照的一个决定生效。
          </p>
        </header>
        {children}
      </div>
    </AuthGuard>
  );
}
