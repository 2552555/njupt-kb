import { AuthGuard } from '@/components/layout/AuthGuard';
import { AdminNav } from '@/app/(app)/admin/AdminNav';

export const metadata = {
  robots: { index: false, follow: false },
};

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard requireReview>
      <div className="space-y-6">
        <header>
          <p className="dn-kicker text-text-secondary">MODERATION</p>
          <h1 className="dn-display mt-1 text-3xl">管理后台</h1>
        </header>
        <AdminNav />
        {children}
      </div>
    </AuthGuard>
  );
}
