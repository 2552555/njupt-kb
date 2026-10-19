import { EmailVerification } from '@/components/auth/EmailVerification';

export const metadata = { title: '验证邮箱', robots: { index: false, follow: false } };

export default async function VerifyEmailPage({ searchParams }: { searchParams: Promise<{ token?: string }> }) {
  const { token } = await searchParams;
  return <EmailVerification token={typeof token === 'string' ? token : ''} />;
}
