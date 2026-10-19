import { PasswordResetForm } from '@/components/auth/PasswordResetForm';

export const metadata = { title: '重置密码', robots: { index: false, follow: false } };

export default async function ResetPasswordPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token } = await searchParams;
  return <PasswordResetForm token={typeof token === 'string' ? token : ''} />;
}
