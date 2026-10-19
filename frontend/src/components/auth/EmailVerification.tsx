'use client';

import Link from 'next/link';
import { useState } from 'react';
import { Button } from '@/components/ui/Button';
import { Notice } from '@/components/common/Notice';
import { confirmEmailVerification, requestEmailVerification } from '@/lib/api/auth';
import { describeError } from '@/lib/api/client';

export function EmailVerification({ token }: { token: string }) {
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setMessage('');
    try {
      if (token) {
        await confirmEmailVerification(token);
        setMessage('邮箱验证完成，请重新登录后继续。');
        window.history.replaceState(null, '', '/verify-email');
      } else {
        const result = await requestEmailVerification(email);
        setMessage(result.message);
      }
    } catch (failure) { setError(describeError(failure).message); }
    finally { setBusy(false); }
  }
  return <div className="space-y-5">
    <h2 className="dn-display text-3xl">验证邮箱</h2>
    <p className="text-sm text-text-secondary">邮箱验证不等于官方学生身份认证。</p>
    {error && <Notice title="验证未完成" tone="warning">{error}<Link className="ml-2 underline" href="/verify-email">重新申请链接</Link></Notice>}
    {message ? <Notice title="请求已处理" tone="info">{message}<Link className="ml-2 underline" href="/login">前往登录</Link></Notice> :
      <form onSubmit={submit} className="space-y-4">
        {!token && <label className="block">注册邮箱<input type="email" required value={email} onChange={event => setEmail(event.target.value)} className="mt-2 w-full border border-control-border bg-surface p-3" /></label>}
        <Button type="submit" isLoading={busy}>{token ? '确认验证邮箱' : '重新发送验证邮件'}</Button>
      </form>}
  </div>;
}
