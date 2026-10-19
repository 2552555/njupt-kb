'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { confirmPasswordReset, requestPasswordReset } from '@/lib/api/auth';
import { describeError } from '@/lib/api/client';

/**
 * 找回密码确认页。链接来自重置邮件（/reset-password?token=…）；
 * 邮件丢失时可以在此手动粘贴令牌，或返回登录页重新申请。
 */
export function PasswordResetForm({ token }: { token: string }) {
  const router = useRouter();
  const [resetToken, setResetToken] = useState(token);
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [done, setDone] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError('');
    setNotice('');
    if (!resetToken.trim()) {
      setError('请填写邮件中的重置令牌');
      return;
    }
    if (password !== confirm) {
      setError('两次输入的密码不一致');
      return;
    }
    if (password.length < 10) {
      setError('密码至少 10 位');
      return;
    }
    setBusy(true);
    try {
      await confirmPasswordReset(resetToken.trim(), password);
      setDone(true);
      // 令牌只应存在 URL 里一次，完成后清理，避免留在浏览器历史中。
      window.history.replaceState(null, '', '/reset-password');
    } catch (failure) {
      setError(describeError(failure).message);
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await requestPasswordReset(email.trim());
      setNotice(result.message);
    } catch (failure) {
      setError(describeError(failure).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <header>
        <p className="dn-kicker text-text-secondary">PASSWORD RESET</p>
        <h2 className="dn-display mt-1 text-3xl">重置密码</h2>
        <p className="mt-2 text-sm text-text-secondary">
          重置成功后所有已登录会话都会失效，需要重新登录。
        </p>
      </header>

      {error && <Notice tone="warning" title="重置未完成">{error}</Notice>}
      {notice && <Notice tone="info" title="申请已处理">{notice}</Notice>}

      {done ? (
        <div className="space-y-4">
          <Notice tone="info" title="密码已重置">
            请使用新密码重新登录。
          </Notice>
          <Button variant="primary" onClick={() => router.replace('/login')}>
            前往登录
          </Button>
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <Input
            label="重置令牌"
            name="reset-token"
            required
            value={resetToken}
            onChange={(event) => setResetToken(event.target.value)}
            placeholder="邮件链接中的 token"
          />
          <Input
            label="新密码"
            type="password"
            name="new-password"
            autoComplete="new-password"
            required
            minLength={10}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          <Input
            label="确认新密码"
            type="password"
            name="confirm-password"
            autoComplete="new-password"
            required
            minLength={10}
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
          />
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" isLoading={busy}>
              重置密码
            </Button>
            <Link
              href="/login"
              className="text-sm text-text-secondary underline underline-offset-2 hover:text-text-primary"
            >
              返回登录
            </Link>
          </div>
        </form>
      )}

      {!done && (
        <section className="border border-divider bg-surface p-4">
          <h3 className="text-sm font-medium text-text-primary">没有收到邮件？</h3>
          <p className="mt-1 text-sm text-text-secondary">
            邮件可能被拦截；可重新申请（不区分邮箱是否已注册）。
          </p>
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <div className="min-w-0 flex-1">
              <Input
                label="注册邮箱"
                type="email"
                name="reset-email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </div>
            <Button variant="secondary" isLoading={busy} onClick={resend}>
              重新申请
            </Button>
          </div>
        </section>
      )}
    </div>
  );
}
