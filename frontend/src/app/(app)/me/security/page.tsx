'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { CheckCircle2, ShieldAlert } from 'lucide-react';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { SkeletonList } from '@/components/ui/Skeleton';
import {
  listSessions,
  requestEmailVerification,
  requestPasswordReset,
  revokeSession,
  updateMe,
} from '@/lib/api/auth';
import { describeError } from '@/lib/api/client';
import { formatDateTime } from '@/lib/utils';
import { useAuthStore } from '@/stores/authStore';

export default function SecurityPage() {
  const { user, setUser, logout } = useAuthStore();
  const queryClient = useQueryClient();
  const [displayName, setDisplayName] = useState(user?.display_name ?? '');
  const [confirmRevoke, setConfirmRevoke] = useState<string | null>(null);

  const sessionsQuery = useQuery({
    queryKey: ['sessions'],
    queryFn: () => listSessions(),
  });

  const profileMutation = useMutation({
    mutationFn: () => updateMe({ display_name: displayName.trim() }),
    onSuccess: (updated) => {
      setUser(updated);
      toast.success('昵称已更新');
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const verifyMutation = useMutation({
    mutationFn: () => requestEmailVerification(user?.email ?? ''),
    onSuccess: (result) => {
      toast.success(result.message);
      if (result.note) toast(result.note);
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const resetMutation = useMutation({
    mutationFn: () => requestPasswordReset(user?.email ?? ''),
    onSuccess: (result) => toast.success(result.message),
    onError: (error) => toast.error(describeError(error).message),
  });

  const revokeMutation = useMutation({
    mutationFn: (sessionId: string) => revokeSession(sessionId),
    onSuccess: () => {
      toast.success('该会话已撤销');
      setConfirmRevoke(null);
      queryClient.invalidateQueries({ queryKey: ['sessions'] });
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  if (!user) return null;

  return (
    <div className="space-y-8">
      <section aria-labelledby="verify-status" className="border border-divider bg-surface p-6">
        <h2 id="verify-status" className="dn-display text-2xl">
          验证状态
        </h2>
        <ul className="mt-3 space-y-2 text-sm">
          <li className="flex items-center gap-2 text-text-primary">
            {user.email_verified ? (
              <>
                <CheckCircle2 className="h-4 w-4 text-emerald" aria-hidden="true" />
                邮箱已验证
                {user.email_verified_at ? `（${formatDateTime(user.email_verified_at)}）` : ''}
              </>
            ) : (
              <>
                <ShieldAlert className="h-4 w-4 text-amber" aria-hidden="true" />
                邮箱未验证：部分操作会受限
              </>
            )}
          </li>
          <li className="flex items-center gap-2 text-text-secondary">
            <ShieldAlert className="h-4 w-4 text-steel" aria-hidden="true" />
            校园身份未验证：身份校验方案确定前不提供该验证，也不代表已完成官方学生身份认证
          </li>
        </ul>
        {!user.email_verified && (
          <div className="mt-4">
            <Button
              variant="secondary"
              onClick={() => verifyMutation.mutate()}
              isLoading={verifyMutation.isPending}
            >
              重新发送验证邮件
            </Button>
          </div>
        )}
      </section>

      <section aria-labelledby="profile" className="border border-divider bg-surface p-6">
        <h2 id="profile" className="dn-display text-2xl">
          基本资料
        </h2>
        <div className="mt-4 max-w-md space-y-4">
          <Input
            label="昵称"
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            maxLength={80}
          />
          <p className="text-sm text-text-secondary">邮箱：{user.email}（暂不支持修改）</p>
          <Button
            onClick={() => profileMutation.mutate()}
            isLoading={profileMutation.isPending}
            disabled={!displayName.trim() || displayName.trim() === user.display_name}
          >
            保存昵称
          </Button>
        </div>
      </section>

      <section aria-labelledby="sessions" className="border border-divider bg-surface p-6">
        <h2 id="sessions" className="dn-display text-2xl">
          登录会话
        </h2>
        <p className="mt-2 text-sm text-text-secondary">
          会话事实保存在服务端；撤销后该设备需要重新登录。
        </p>
        <div className="mt-4">
          {sessionsQuery.isLoading ? (
            <SkeletonList count={2} />
          ) : sessionsQuery.error ? (
            <Notice
              tone="warning"
              title="会话列表暂时无法加载"
              action={<Button variant="secondary" onClick={() => sessionsQuery.refetch()}>重试</Button>}
            >
              {describeError(sessionsQuery.error).message}
            </Notice>
          ) : sessionsQuery.data && sessionsQuery.data.items.length > 0 ? (
            <ul className="divide-y divide-divider border border-divider">
              {sessionsQuery.data.items.map((session) => (
                <li
                  key={session.id}
                  className="px-4 py-3"
                >
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-sm text-text-primary">
                        {session.user_agent ?? '未知设备'}
                        {session.current && (
                          <span className="ml-2 bg-emerald px-2 py-0.5 text-xs font-semibold text-on-color">
                            当前会话
                          </span>
                        )}
                      </p>
                      <p className="mt-1 text-xs text-text-secondary">
                        最近活动 {session.last_seen_at ? formatDateTime(session.last_seen_at) : '未知'} ·
                        到期 {formatDateTime(session.expires_at)}
                      </p>
                    </div>
                    {confirmRevoke === session.id ? (
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-xs text-text-secondary">
                          撤销后该设备需重新登录，不会影响其他会话。
                        </span>
                        <Button
                          variant="danger"
                          onClick={() => revokeMutation.mutate(session.id)}
                          isLoading={revokeMutation.isPending}
                        >
                          确认撤销
                        </Button>
                        <Button variant="ghost" onClick={() => setConfirmRevoke(null)}>
                          取消
                        </Button>
                      </div>
                    ) : (
                      <Button variant="ghost" onClick={() => setConfirmRevoke(session.id)}>
                        撤销
                      </Button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <Notice tone="info" title="没有其他活跃会话">
              这里只会列出你自己的会话。
            </Notice>
          )}
        </div>
      </section>

      <section aria-labelledby="password" className="border border-divider bg-surface p-6">
        <h2 id="password" className="dn-display text-2xl">
          密码
        </h2>
        <p className="mt-2 text-sm text-text-secondary">
          重置密码后，所有登录会话会失效，需要重新登录。
        </p>
        <div className="mt-4">
          <Button
            variant="secondary"
            onClick={() => resetMutation.mutate()}
            isLoading={resetMutation.isPending}
          >
            发送重置密码说明
          </Button>
        </div>
      </section>

      <section aria-labelledby="danger" className="border border-divider border-l-4 border-l-crimson bg-surface p-6">
        <h2 id="danger" className="dn-display text-2xl">
          注销与退出
        </h2>
        <div className="mt-4 space-y-4">
          <div>
            <Button variant="secondary" onClick={() => void logout()}>
              退出当前会话
            </Button>
          </div>
          <Notice tone="warning" title="账号注销入口暂不可用">
            后端尚未提供账号注销接口，因此这里不提供无法完成的按钮。注销涉及个人资料匿名化、
            公开内容署名处理与审计记录保留，需要在上线前确定保留期限与导出范围后再开放。
          </Notice>
        </div>
      </section>
    </div>
  );
}
