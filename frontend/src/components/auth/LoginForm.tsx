'use client';

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { FormEvent, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import toast from 'react-hot-toast';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { describeError } from '@/lib/api/client';
import { requestPasswordReset } from '@/lib/api/auth';
import { useAuthStore } from '@/stores/authStore';

export function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const nextPath = searchParams.get('next');
  const { login } = useAuthStore();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showReset, setShowReset] = useState(false);

  const loginMutation = useMutation({
    mutationFn: () => login(email.trim(), password),
    onSuccess: () => {
      const target = nextPath && nextPath.startsWith('/') ? nextPath : '/';
      router.replace(target);
    },
  });

  const resetMutation = useMutation({
    mutationFn: () => requestPasswordReset(email.trim()),
    onSuccess: (result) => {
      toast.success(result.message);
      setShowReset(false);
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    loginMutation.mutate();
  }

  const error = loginMutation.error ? describeError(loginMutation.error) : null;

  return (
    <div className="space-y-6">
      <header>
        <p className="dn-kicker text-text-secondary">SIGN IN</p>
        <h1 className="dn-display mt-1 text-3xl">登录</h1>
        <p className="mt-2 text-sm text-text-secondary">
          使用邮箱与密码登录。会话通过安全 Cookie 维持，不会在浏览器里保存令牌。
        </p>
      </header>

      {error && (
        <Notice tone="error" title="登录未完成">
          {error.message}
          {error.requestId ? `（请求编号 ${error.requestId}）` : ''}
          <p className="mt-1">请确认邮箱与密码正确后重试；多次失败会触发限流。</p>
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
          label="密码"
          type="password"
          name="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <Button type="submit" className="w-full" isLoading={loginMutation.isPending}>
          登录
        </Button>
      </form>

      <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
        <Link href="/register" className="text-text-primary underline underline-offset-2">
          还没有账号？注册
        </Link>
        <button
          type="button"
          onClick={() => setShowReset((open) => !open)}
          className="text-text-secondary underline underline-offset-2 hover:text-text-primary"
        >
          忘记密码
        </button>
      </div>

      {showReset && (
        <div className="border border-divider bg-surface p-4">
          <p className="text-sm text-text-secondary">
            输入注册邮箱，我们会发送重置说明（不区分该邮箱是否已注册）。
          </p>
          <div className="mt-3">
            <Button
              variant="secondary"
              onClick={() => {
                if (!email.trim()) {
                  toast.error('请先填写邮箱');
                  return;
                }
                resetMutation.mutate();
              }}
              isLoading={resetMutation.isPending}
            >
              发送重置说明
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
