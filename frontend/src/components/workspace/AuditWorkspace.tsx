'use client';
import { useState } from 'react';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import type { AuditItem } from '@/lib/api/workspaces';
import { formatDateTime } from '@/lib/utils';
import { AuditExportPanel } from './AuditExportPanel';
import { ManagementGuard, QueryFeedback, useWorkspaceList } from './Workspace';

export function AuditWorkspace({ initialType = '', initialId = '' }: { initialType?: string; initialId?: string }) {
  return <ManagementGuard permission="audit.view"><Audit initialType={initialType} initialId={initialId} /></ManagementGuard>;
}
function Audit({ initialType, initialId }: { initialType: string; initialId: string }) {
  const [action, setAction] = useState('');
  const [objectType, setObjectType] = useState(initialType);
  const [objectId, setObjectId] = useState(initialId);
  const [actor, setActor] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [filters, setFilters] = useState<Record<string, string>>({ ...(initialType ? { object_type: initialType } : {}), ...(initialId ? { object_id: initialId } : {}) });
  const query = useWorkspaceList<AuditItem>('audit-records', '/admin/audit', filters);
  const rows = query.data?.pages.flatMap(page => page.items) ?? [];
  return <section className="space-y-5"><h2 className="dn-display text-2xl">审计检索</h2>
    <form className="grid items-end gap-3 sm:grid-cols-2 lg:grid-cols-3" onSubmit={event => { event.preventDefault(); setFilters(Object.fromEntries(Object.entries({ action: action.trim(), object_type: objectType.trim(), object_id: objectId.trim(), actor_id: actor.trim(), created_from: from ? new Date(from).toISOString() : '', created_to: to ? new Date(to).toISOString() : '' }).filter(([, value]) => value))); }}>
      <Input label="操作类型" placeholder="如 account.suspend" value={action} onChange={e => setAction(e.target.value)} />
      <Input label="对象类型" placeholder="如 resource、user" value={objectType} onChange={e => setObjectType(e.target.value)} />
      <Input label="对象 ID" value={objectId} onChange={e => setObjectId(e.target.value)} />
      <Input label="操作者 ID" value={actor} onChange={e => setActor(e.target.value)} pattern="[0-9a-fA-F-]{36}" />
      <Input label="开始时间" type="datetime-local" value={from} onChange={e => setFrom(e.target.value)} />
      <Input label="结束时间" type="datetime-local" value={to} onChange={e => setTo(e.target.value)} />
      <div className="flex gap-3"><Button type="submit">查询</Button><Button type="button" variant="secondary" onClick={() => { setAction(''); setObjectType(''); setObjectId(''); setActor(''); setFrom(''); setTo(''); setFilters({}); }}>清空</Button></div>
    </form>
    <AuditExportPanel />
    <QueryFeedback pending={query.isPending} error={query.error} empty={!rows.length} retry={() => query.refetch()} />
    <ul className="space-y-3">{rows.map(row => <li key={row.id} className="space-y-3 border border-divider p-5"><header className="flex flex-wrap justify-between gap-3"><h3 className="break-all font-semibold">{row.action}</h3><time className="text-sm text-text-secondary">{formatDateTime(row.created_at)}</time></header><p className="break-all text-sm">{row.object_type} · {row.object_id ?? '—'}</p>{row.reason && <p className="whitespace-pre-wrap text-sm">{row.reason}</p>}<details className="text-sm"><summary className="cursor-pointer">变更详情</summary><dl className="mt-3 space-y-2 break-all text-text-secondary"><div>操作者：{row.actor_id ?? '系统'}</div><div>请求 ID：{row.request_id ?? '—'}</div></dl><div className="mt-3 grid gap-3 lg:grid-cols-2">{[['变更前', row.before_state], ['变更后', row.after_state]].map(([label, value]) => <section key={String(label)} className="min-w-0"><h4 className="font-medium">{String(label)}</h4><pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all bg-canvas p-3 text-xs">{JSON.stringify(value, null, 2)}</pre></section>)}</div></details></li>)}</ul>
    {query.hasNextPage && <Button onClick={() => query.fetchNextPage()} isLoading={query.isFetchingNextPage}>更多审计记录</Button>}
  </section>;
}
