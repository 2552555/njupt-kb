'use client';
import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { api, describeError } from '@/lib/api/client';
import { useAuthStore } from '@/stores/authStore';

interface MFAStatus { enabled: boolean; configured: boolean; recovery_remaining: number; recently_verified: boolean }

export function MfaPanel() {
  const user = useAuthStore(s => s.user);
  const [password, setPassword] = useState('');
  const [otp, setOtp] = useState('');
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [codes, setCodes] = useState<string[]>([]);
  const [confirmDisable, setConfirmDisable] = useState(false);
  const query = useQuery({
    queryKey: ['mfa', user?.id], enabled: !!user,
    queryFn: async () => (await api.get<MFAStatus>('/me/mfa')).data,
  });
  const mutation = useMutation({
    mutationFn: async (action: 'setup' | 'confirm' | 'reauth' | 'disable') => {
      if (action === 'setup') {
        const { data } = await api.post<{ secret: string; otpauth_uri: string }>('/me/mfa/setup', { password });
        setSetup(data);
      } else if (action === 'confirm') {
        const { data } = await api.post<{ recovery_codes: string[] }>('/me/mfa/confirm', { otp });
        setSetup(null);
        setCodes(data.recovery_codes);
        toast.success('验证器已启用，请保存恢复码');
      } else {
        const { data } = await api.post<{ message: string }>(`/me/mfa/${action}`, { password, otp });
        toast.success(data.message);
        setConfirmDisable(false);
      }
      setPassword('');
      setOtp('');
      await query.refetch();
    },
    onError: error => toast.error(describeError(error).message),
  });
  return <section aria-labelledby="mfa-title" className="space-y-4 border border-divider bg-surface p-6">
    <h2 id="mfa-title" className="dn-display text-2xl">验证器与敏感操作认证</h2>
    <p className="text-sm text-text-secondary">每个动态代码或恢复码只能使用一次。绑定、禁用和重新认证会轮换当前会话并撤销其他设备会话。</p>
    {query.error ? <Notice title="验证器状态暂不可用" tone="warning" action={<Button onClick={() => query.refetch()}>重试</Button>}>{describeError(query.error).message}</Notice>
      : query.isPending ? <p role="status">正在读取验证器状态…</p>
        : !query.data?.configured ? <Notice title="验证器服务尚未配置">管理员需先配置独立加密密钥，配置完成后可在此绑定。生产环境敏感管理操作需要验证器认证。</Notice>
          : <>
            <p className="text-sm">{query.data.enabled ? `已启用 · 剩余 ${query.data.recovery_remaining} 个恢复码 · ${query.data.recently_verified ? '近期已认证' : '需要重新认证'}` : '尚未启用'}</p>
            {codes.length > 0 && <div className="space-y-3 border border-crimson p-4">
              <p className="font-medium">恢复码只显示这一次，请存入私密位置。丢失验证器和全部恢复码后不能仅凭邮箱关闭 MFA。</p>
              <ul className="grid gap-2 sm:grid-cols-2">{codes.map(code => <li key={code}><code className="break-all">{code}</code></li>)}</ul>
              <Button variant="secondary" onClick={() => setCodes([])}>我已保存，隐藏恢复码</Button>
            </div>}
            <fieldset disabled={mutation.isPending} className="space-y-3">
              {!setup && <Input label="当前密码" type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} />}
              {setup ? <>
                <p className="text-sm">在验证器中手动添加账号，密钥为：</p>
                <p className="select-all break-all border border-divider p-3 font-mono">{setup.secret}</p>
                <p className="text-xs text-text-secondary">时间型、6 位、30 秒。绑定请求十分钟内有效。</p>
                <Input label="验证器当前代码" autoComplete="one-time-code" value={otp} onChange={event => setOtp(event.target.value)} />
                <Button disabled={!otp.trim()} onClick={() => mutation.mutate('confirm')}>验证并启用</Button>
                <Button variant="secondary" onClick={() => { setSetup(null); setOtp(''); }}>取消绑定</Button>
              </> : query.data.enabled ? <>
                <Input label="验证器代码或一次性恢复码" autoComplete="one-time-code" value={otp} onChange={event => setOtp(event.target.value)} />
                <Button disabled={!password || !otp.trim()} onClick={() => mutation.mutate('reauth')}>重新认证敏感操作</Button>
                <label className="flex min-h-11 items-center gap-2 text-sm"><input type="checkbox" checked={confirmDisable} onChange={event => setConfirmDisable(event.target.checked)} />确认禁用当前验证器并使剩余恢复码失效</label>
                <Button variant="secondary" disabled={!confirmDisable || !password || !otp.trim()} onClick={() => mutation.mutate('disable')}>禁用验证器</Button>
              </> : <Button disabled={!password} onClick={() => mutation.mutate('setup')}>开始绑定验证器</Button>}
            </fieldset>
          </>}
  </section>;
}
