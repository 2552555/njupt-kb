'use client';

import { MfaPanel } from '@/components/workspace/MfaPanel';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { CheckCircle2, Download, ShieldAlert } from 'lucide-react';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { SkeletonList } from '@/components/ui/Skeleton';
import {
  changePassword,
  deleteAccount,
  exportMyData,
  listSessions,
  requestEmailVerification,
  requestPasswordReset,
  revokeOtherSessions,
  revokeSession,
  updateMe,
} from '@/lib/api/auth';
import { describeError } from '@/lib/api/client';
import { formatDateTime } from '@/lib/utils';
import { useAuthStore } from '@/stores/authStore';

// 与后端 identity.service.DELETION_CONFIRM_PHRASE 保持一致；仅用于前端即时校验。
const DELETION_CONFIRM_PHRASE = '注销账号';

export default function SecurityPage() {
  const { user, setUser, logout } = useAuthStore();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [displayName, setDisplayName] = useState(user?.display_name ?? '');
  const [confirmRevoke, setConfirmRevoke] = useState<string | null>(null);
  const [revokingOthers, setRevokingOthers] = useState(false);
  const [password, setPassword] = useState('');
  const [confirmPhrase, setConfirmPhrase] = useState('');
  const [confirmingDeletion, setConfirmingDeletion] = useState(false);
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  const sessionsQuery = useQuery({
    queryKey: ['sessions', user?.id],
    queryFn: () => listSessions(),
    enabled: !!user,
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

  const revokeOthersMutation = useMutation({
    mutationFn: () => revokeOtherSessions(),
    onSuccess: (result) => {
      toast.success(result.message);
      setRevokingOthers(false);
      queryClient.invalidateQueries({ queryKey: ['sessions'] });
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const changePasswordMutation = useMutation({
    mutationFn: () =>
      changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    onSuccess: (result) => {
      toast.success(result.message);
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
      // 其他设备会话已被服务端撤销，刷新列表。
      queryClient.invalidateQueries({ queryKey: ['sessions'] });
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const passwordMismatch =
    confirmPassword.length > 0 && confirmPassword !== newPassword;
  const canSubmitPassword =
    currentPassword.length > 0 &&
    newPassword.length > 0 &&
    confirmPassword.length > 0 &&
    !passwordMismatch;

  const exportMutation = useMutation({
    mutationFn: () => exportMyData(),
    onSuccess: ({ blob, filename }) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      toast.success('数据导出已开始下载');
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const deletionMutation = useMutation({
    mutationFn: () => deleteAccount({ password, confirm: confirmPhrase.trim() }),
    onSuccess: (result) => {
      toast.success(result.message);
      // 服务端已撤销全部会话；清空本地登录态并回到首页。
      setUser(null);
      setPassword('');
      setConfirmPhrase('');
      setConfirmingDeletion(false);
      router.push('/');
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const canSubmitDeletion =
    password.length > 0 && confirmPhrase.trim() === DELETION_CONFIRM_PHRASE;

  if (!user) return null;

  return (
    <div className="space-y-8">
      <MfaPanel />
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
          撤销后该设备需要重新登录。
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
            </Notice>
          )}
        </div>
        <div className="mt-4">
          {revokingOthers ? (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm text-text-secondary">
                将撤销除当前设备外的全部会话，其他设备需要重新登录。确认继续？
              </span>
              <Button
                variant="danger"
                onClick={() => revokeOthersMutation.mutate()}
                isLoading={revokeOthersMutation.isPending}
              >
                确认退出其他设备
              </Button>
              <Button
                variant="ghost"
                onClick={() => setRevokingOthers(false)}
                disabled={revokeOthersMutation.isPending}
              >
                取消
              </Button>
            </div>
          ) : (
            <Button variant="secondary" onClick={() => setRevokingOthers(true)}>
              退出所有其他设备
            </Button>
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

        <div className="mt-6 border-t border-divider pt-6">
          <h3 className="text-base font-medium text-text-primary">修改密码</h3>
          <p className="mt-2 text-sm text-text-secondary">
            修改成功后，其他设备上的会话会退出，当前设备保持登录。
          </p>
          <div className="mt-4 max-w-md space-y-4">
            <Input
              label="当前密码"
              type="password"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
            />
            <Input
              label="新密码"
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
            />
            <Input
              label="确认新密码"
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              error={passwordMismatch ? '两次输入的新密码不一致' : undefined}
            />
            {changePasswordMutation.isError && (
              <Notice
                tone="error"
                title="密码未修改"
                action={
                  <Button
                    variant="danger"
                    onClick={() => changePasswordMutation.mutate()}
                    isLoading={changePasswordMutation.isPending}
                    disabled={!canSubmitPassword}
                  >
                    重试
                  </Button>
                }
              >
                {describeError(changePasswordMutation.error).message}
              </Notice>
            )}
            <Button
              onClick={() => changePasswordMutation.mutate()}
              isLoading={changePasswordMutation.isPending}
              disabled={!canSubmitPassword}
            >
              修改密码
            </Button>
          </div>
        </div>
      </section>

      <section aria-labelledby="danger" className="border border-divider border-l-4 border-l-crimson bg-surface p-6">
        <h2 id="danger" className="dn-display text-2xl">
          注销与退出
        </h2>

        <div className="mt-4 flex flex-wrap gap-3">
          <Button variant="secondary" onClick={() => void logout()}>
            退出当前会话
          </Button>
          <Button
            variant="secondary"
            onClick={() => exportMutation.mutate()}
            isLoading={exportMutation.isPending}
          >
            <Download className="h-4 w-4" aria-hidden="true" />
            导出我的数据
          </Button>
        </div>
        <div className="mt-6 border-t border-divider pt-6">
          <h3 className="text-base font-medium text-text-primary">注销账号</h3>
          <Notice tone="error" title="注销将立即生效且不可撤销">
            注销后该账号无法登录，昵称、头像与个人资料将被匿名化，全部登录会话会被撤销；
            你上传的资料、评论、评分与审计记录作为业务事实保留（评论不匿名化，对外仅显示「已注销用户」）。
            邮箱与审计/业务事实在 30 天保留期内继续保留，供申诉与对账，期满由运维清理。
          </Notice>

          <div className="mt-4 max-w-md space-y-4">
            <Input
              label="当前密码"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            <Input
              label="确认词"
              value={confirmPhrase}
              onChange={(event) => setConfirmPhrase(event.target.value)}
              placeholder={`请输入「${DELETION_CONFIRM_PHRASE}」`}
              maxLength={40}
            />

            {deletionMutation.isError && (
              <Notice
                tone="error"
                title="注销未完成"
                action={
                  <Button
                    variant="danger"
                    onClick={() => deletionMutation.mutate()}
                    isLoading={deletionMutation.isPending}
                  >
                    重试
                  </Button>
                }
              >
                {describeError(deletionMutation.error).message}
              </Notice>
            )}

            {confirmingDeletion ? (
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-sm text-text-secondary">
                  确认注销？该操作立即生效，无法恢复。
                </span>
                <Button
                  variant="danger"
                  onClick={() => deletionMutation.mutate()}
                  isLoading={deletionMutation.isPending}
                  disabled={!canSubmitDeletion}
                >
                  确认注销
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => setConfirmingDeletion(false)}
                  disabled={deletionMutation.isPending}
                >
                  取消
                </Button>
              </div>
            ) : (
              <Button
                variant="danger"
                onClick={() => setConfirmingDeletion(true)}
                disabled={!canSubmitDeletion}
              >
                注销账号
              </Button>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
