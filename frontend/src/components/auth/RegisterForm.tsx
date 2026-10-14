'use client';

import Link from 'next/link';
import { FormEvent, useState } from 'react';
import { useMutation } from '@tanstack/react-query';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { register } from '@/lib/api/auth';
import { describeError } from '@/lib/api/client';

export function RegisterForm() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');

  const registerMutation = useMutation({
    mutationFn: () =>
      register({
        email: email.trim(),
        password,
        display_name: displayName.trim() || undefined,
      }),
  });

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    registerMutation.mutate();
  }

  const error = registerMutation.error ? describeError(registerMutation.error) : null;
  const result = registerMutation.data;

  if (result) {
    return (
      <div className="space-y-6">
        <header>
          <p className="dn-kicker text-text-secondary">REGISTERED</p>
          <h1 className="dn-display mt-1 text-3xl">注册成功</h1>
        </header>
        <Notice tone="success" title={result.message}>
          <p>
            账号状态：{result.status}
            {result.email_verification_sent ? '，验证邮件已发送。' : '。'}
          </p>
          {result.note && <p className="mt-1">{result.note}</p>}
          <p className="mt-1">
            邮箱验证只证明你当时控制该邮箱，不代表完成官方学生身份认证。
          </p>
        </Notice>
        <Link
          href="/login"
          className="inline-flex min-h-11 items-center bg-brand px-4 text-sm font-medium text-on-color hover:bg-brand/90"
        >
          前往登录
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <header>
        <p className="dn-kicker text-text-secondary">CREATE ACCOUNT</p>
        <h1 className="dn-display mt-1 text-3xl">注册</h1>
        <p className="mt-2 text-sm text-text-secondary">
          使用邮箱注册。注册后需要完成邮箱验证才能使用上传等操作。
        </p>
      </header>

      {error && (
        <Notice tone="error" title="注册未完成">
          {error.message}
          <p className="mt-1">请检查填写内容；邮箱已注册时可以直接登录。</p>
        </Notice>
      )}

      <form onSubmit={handleSubmit} className="space-y-4">
        <Input
          label="邮箱"
          type="email"
          name="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <Input
          label="昵称（可选）"
          name="display_name"
          autoComplete="nickname"
          maxLength={80}
          value={displayName}
          onChange={(event) => setDisplayName(event.target.value)}
        />
        <Input
          label="密码"
          type="password"
          name="password"
          autoComplete="new-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <p className="text-xs text-text-secondary">
          建议使用较长的密码，避免与其他网站重复使用。
        </p>
        <Button type="submit" className="w-full" isLoading={registerMutation.isPending}>
          注册
        </Button>
      </form>

      <p className="text-sm text-text-secondary">
        已有账号？
        <Link href="/login" className="ml-1 text-text-primary underline underline-offset-2">
          去登录
        </Link>
      </p>
    </div>
  );
}
