'use client';
import Link from 'next/link';
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { api, describeError } from '@/lib/api/client';
import type { EntitlementItem } from '@/lib/api/workspaces';
import { formatDateTime } from '@/lib/utils';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { fieldClass, ManagementGuard, QueryFeedback, useWorkspaceList } from './Workspace';

const capabilities: Record<string, string> = { read_metadata: '查看资料信息', preview: '预览', download_original: '下载原件', read_full: '完整阅读' };
const uuidPattern = '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}';
function entitlementState(item: EntitlementItem) {
  if (item.revoked_at) return '已撤销';
  if (item.expires_at && new Date(item.expires_at).getTime() <= Date.now()) return '已到期';
  if (item.starts_at && new Date(item.starts_at).getTime() > Date.now()) return '待生效';
  return '有效';
}
export function EntitlementWorkspace({ initialUser = '' }: { initialUser?: string }) { return <ManagementGuard permission="entitlement.grant"><Entitlements initialUser={initialUser} /></ManagementGuard>; }
function Entitlements({ initialUser }: { initialUser: string }) {
  const client = useQueryClient();
  const [filter, setFilter] = useState(initialUser);
  const [filterText, setFilterText] = useState(initialUser);
  const [open, setOpen] = useState(false);
  const [userId, setUserId] = useState(initialUser);
  const [scopeId, setScopeId] = useState('');
  const [capability, setCapability] = useState('download_original');
  const [expiry, setExpiry] = useState('');
  const [confirm, setConfirm] = useState(false);
  const [revoke, setRevoke] = useState<EntitlementItem | null>(null);
  const [reason, setReason] = useState('');
  const query = useWorkspaceList<EntitlementItem>('managed-entitlements', '/admin/entitlements', filter ? { user_id: filter } : {});
  const rows = query.data?.pages.flatMap(page => page.items) ?? [];
  const invalidate = () => client.invalidateQueries({ queryKey: ['managed-entitlements'] });
  const create = useMutation({
    mutationFn: () => api.post('/admin/entitlements', { user_id: userId, scope_type: 'explicit_grant', scope_id: scopeId, capability, source_type: 'admin_grant', expires_at: expiry ? new Date(expiry).toISOString() : null }),
    onSuccess: () => { setOpen(false); setConfirm(false); setScopeId(''); invalidate(); toast.success('访问权益已授予'); },
    onError: error => toast.error(describeError(error).message),
  });
  const remove = useMutation({
    mutationFn: () => { if (!revoke || !reason.trim()) throw new Error('请填写撤销理由'); return api.post(`/admin/entitlements/${revoke.id}/revoke`, { reason: reason.trim() }); },
    onSuccess: () => { setRevoke(null); invalidate(); toast.success('权益已撤销，新的访问申请将重新校验'); },
    onError: error => {
      const failure = describeError(error);
      toast.error(failure.message);
      if (failure.code === 'ENTITLEMENT_ALREADY_REVOKED') setRevoke(null);
      invalidate();
    },
  });
  return <section className="space-y-5"><header className="flex flex-wrap justify-between gap-3"><h2 className="dn-display text-2xl">访问权益</h2><Button onClick={() => { setConfirm(false); setUserId(filter); setOpen(true); }}>授予权益</Button></header>
    <form className="flex items-end gap-3" onSubmit={event => { event.preventDefault(); setFilter(filterText.trim()); }}><Input label="按用户查询" value={filterText} onChange={e => setFilterText(e.target.value)} placeholder="用户 ID" pattern={uuidPattern} /><Button type="submit">查询</Button><Button type="button" variant="secondary" onClick={() => { setFilter(''); setFilterText(''); }}>全部</Button></form>
    <QueryFeedback pending={query.isPending} error={query.error} empty={!rows.length} retry={() => query.refetch()} />
    <ul className="space-y-3">{rows.map(row => <li key={row.id} className="space-y-3 border border-divider p-5"><header className="flex flex-wrap justify-between gap-3"><h3 className="font-semibold">{capabilities[row.capability] ?? row.capability} · {entitlementState(row)}</h3>{!row.revoked_at && <Button variant="secondary" onClick={() => { setRevoke(row); setReason(''); }}>撤销</Button>}</header><p className="break-all text-sm">用户：{row.user_id}</p><p className="break-all text-sm">授权对象：{row.scope_id}</p><p className="text-sm text-text-secondary">到期：{row.expires_at ? formatDateTime(row.expires_at) : '长期有效'} · 来源：{row.source_type}</p>{row.revoked_reason && <p className="text-sm">撤销理由：{row.revoked_reason}</p>}<Link href={`/admin/audit?object_type=entitlement&object_id=${row.id}`} className="inline-block text-sm text-brand underline">审计记录</Link></li>)}</ul>
    {query.hasNextPage && <Button onClick={() => query.fetchNextPage()} isLoading={query.isFetchingNextPage}>更多权益</Button>}
    <Modal isOpen={open} onClose={() => { if (!create.isPending) setOpen(false); }} title="授予访问权益"><form className="space-y-4" onSubmit={event => { event.preventDefault(); if (!confirm) setConfirm(true); else create.mutate(); }}>
      {confirm ? <div className="space-y-3 break-all text-sm"><p>用户：{userId}</p><p>资料/版本/课程 ID：{scopeId}</p><p>能力：{capabilities[capability]}</p><p>到期：{expiry ? formatDateTime(new Date(expiry).toISOString()) : '长期有效'}</p><p>本次为管理员授权，不创建订单或付费记录。</p></div> : <><Input label="用户 ID" value={userId} onChange={e => setUserId(e.target.value)} required pattern={uuidPattern} /><Input label="资料 / 版本 / 课程 ID" value={scopeId} onChange={e => setScopeId(e.target.value)} required pattern={uuidPattern} /><label className="block text-sm">访问能力<select className={fieldClass} value={capability} onChange={e => setCapability(e.target.value)}>{['read_metadata', 'preview', 'download_original'].map(value => <option key={value} value={value}>{capabilities[value]}</option>)}</select></label><Input label="到期时间（可选）" type="datetime-local" value={expiry} onChange={e => setExpiry(e.target.value)} /></>}
      <div className="flex gap-3"><Button type="submit" isLoading={create.isPending}>{confirm ? '确认授予' : '核对授权'}</Button><Button type="button" variant="secondary" disabled={create.isPending} onClick={() => confirm ? setConfirm(false) : setOpen(false)}>{confirm ? '返回修改' : '取消'}</Button></div>
    </form></Modal>
    <Modal isOpen={!!revoke} onClose={() => { if (!remove.isPending) setRevoke(null); }} title="撤销访问权益"><form className="space-y-4" onSubmit={event => { event.preventDefault(); remove.mutate(); }}><p className="break-all text-sm">{revoke?.user_id} · {revoke?.scope_id}</p><p className="text-sm text-text-secondary">撤销后不再签发新链接；已签发短期链接在有效期内仍可能可用。</p><label className="block text-sm">撤销理由<textarea className={fieldClass} value={reason} onChange={e => setReason(e.target.value)} required maxLength={255} rows={3} /></label><div className="flex gap-3"><Button type="submit" isLoading={remove.isPending} disabled={!reason.trim()}>确认撤销</Button><Button type="button" variant="secondary" onClick={() => setRevoke(null)} disabled={remove.isPending}>取消</Button></div></form></Modal>
  </section>;
}
