import { AuthGuard } from '@/components/layout/AuthGuard';
import { MeNav } from '@/components/layout/MeNav';

export default function MeLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard>
      <div className="space-y-6">
        <header>
          <p className="dn-kicker text-text-secondary">ACCOUNT</p>
          <h1 className="dn-display mt-1 text-3xl">我的</h1>
        </header>
        <MeNav />
        {children}
      </div>
    </AuthGuard>
  );
}
