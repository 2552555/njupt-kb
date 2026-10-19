'use client';
import Link from 'next/link';
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { api, describeError } from '@/lib/api/client';
import type { ManagedUser } from '@/lib/api/workspaces';
import { formatDateTime } from '@/lib/utils';
import { useAuthStore } from '@/stores/authStore';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { fieldClass, ManagementGuard, QueryFeedback, useWorkspaceList } from './Workspace';

const states: Record<string, string> = { active: '正常', suspended: '已暂停', pending_verification: '待验证', banned: '已封禁', deleted: '已注销' };
const roles: Record<string, string> = { user: '用户', contributor: '贡献者', reviewer: '审核员', admin: '管理员' };
export function UserManagement() { return <ManagementGuard><Users /></ManagementGuard>; }
function Users() {
  const user = useAuthStore(s => s.user);
  const client = useQueryClient();
  const [search, setSearch] = useState('');
  const [keyword, setKeyword] = useState('');
  const [status, setStatus] = useState('');
  const [role, setRole] = useState('');
  const [target, setTarget] = useState<ManagedUser | null>(null);
  const [reason, setReason] = useState('');
  const query = useWorkspaceList<ManagedUser>('managed-users', '/admin/users', { keyword, status, role });
  const rows = query.data?.pages.flatMap(page => page.items) ?? [];
  const action = useMutation({
    mutationFn: () => {
      if (!target || !reason.trim()) throw new Error('请填写处理理由');
      return api.post(`/admin/users/${target.id}/${target.status === 'suspended' ? 'reinstate' : 'suspend'}`, { reason: reason.trim() });
    },
    onSuccess: () => { setTarget(null); setReason(''); client.invalidateQueries({ queryKey: ['managed-users'] }); toast.success('账号状态已更新'); },
    onError: error => {
      const failure = describeError(error);
      toast.error(failure.message);
      if (failure.code === 'ACCOUNT_STATE_CHANGED') setTarget(null);
      client.invalidateQueries({ queryKey: ['managed-users'] });
    },
  });
  return <section className="space-y-5">
    <h2 className="dn-display text-2xl">用户治理</h2>
    <form className="grid gap-3 md:grid-cols-[1fr_10rem_10rem_auto]" onSubmit={event => { event.preventDefault(); setKeyword(search.trim()); }}>
      <Input aria-label="查找用户" placeholder="邮箱或昵称" value={search} onChange={e => setSearch(e.target.value)} maxLength={120} />
      <select aria-label="账号状态" className={fieldClass} value={status} onChange={e => setStatus(e.target.value)}><option value="">全部状态</option>{Object.entries(states).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
      <select aria-label="用户角色" className={fieldClass} value={role} onChange={e => setRole(e.target.value)}><option value="">全部角色</option>{Object.entries(roles).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
      <Button type="submit">查询</Button>
    </form>
    <QueryFeedback pending={query.isPending} error={query.error} empty={!rows.length} retry={() => query.refetch()} />
    <ul className="space-y-3">{rows.map(row => <li key={row.id} className="flex flex-wrap items-start justify-between gap-4 border border-divider p-5">
      <div className="min-w-0 space-y-2"><h3 className="font-semibold">{row.display_name} · {roles[row.role]}</h3><p className="break-all text-sm">{row.email}</p><p className="text-sm text-text-secondary">{states[row.status]} · {row.email_verified ? '邮箱已验证' : '邮箱未验证'} · {formatDateTime(row.created_at)}</p><details className="text-xs text-text-secondary"><summary>账号标识</summary><p className="break-all">{row.id}</p></details><div className="flex gap-4 text-sm"><Link href={`/admin/entitlements?user_id=${row.id}`} className="text-brand underline">访问权益</Link><Link href={`/admin/audit?object_type=user&object_id=${row.id}`} className="text-brand underline">审计记录</Link></div></div>
      {row.id !== user?.id && row.role !== 'admin' && ['active', 'suspended'].includes(row.status) && <Button variant="secondary" onClick={() => { setTarget(row); setReason(''); }}>{row.status === 'suspended' ? '恢复账号' : '暂停账号'}</Button>}
    </li>)}</ul>
    {query.hasNextPage && <Button onClick={() => query.fetchNextPage()} isLoading={query.isFetchingNextPage}>更多用户</Button>}
    <Modal isOpen={!!target} onClose={() => { if (!action.isPending) setTarget(null); }} title={target?.status === 'suspended' ? '恢复账号' : '暂停账号'}>
      <form className="space-y-4" onSubmit={event => { event.preventDefault(); action.mutate(); }}>
        <p className="break-all">{target?.display_name} · {target?.email}</p>
        {target?.status !== 'suspended' && <p className="text-sm text-text-secondary">暂停后撤销现有会话；资料与业务记录继续保留。</p>}
        <label className="block text-sm">处理理由（必填）<textarea className={fieldClass} value={reason} onChange={e => setReason(e.target.value)} maxLength={1000} rows={3} required /></label>
        <div className="flex gap-3"><Button type="submit" isLoading={action.isPending} disabled={!reason.trim()}>确认处理</Button><Button type="button" variant="secondary" disabled={action.isPending} onClick={() => setTarget(null)}>取消</Button></div>
      </form>
    </Modal>
  </section>;
}
