import type { Metadata } from 'next';
import { Suspense } from 'react';

import { LoginForm } from '@/components/auth/LoginForm';

export const metadata: Metadata = {
  title: '登录',
  robots: { index: false, follow: false },
};

export default function LoginPage() {
  return (
    <Suspense fallback={<p className="text-sm text-text-secondary">加载登录表单…</p>}>
      <LoginForm />
    </Suspense>
  );
}
