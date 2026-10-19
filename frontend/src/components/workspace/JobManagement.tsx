'use client';
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { api, describeError } from '@/lib/api/client';
import type { ManagedJob } from '@/lib/api/workspaces';
import { formatDateTime } from '@/lib/utils';
import { hasPermission, useAuthStore } from '@/stores/authStore';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { fieldClass, ManagementGuard, QueryFeedback, useWorkspaceList } from './Workspace';

const states: Record<string, string> = { queued: '排队中', running: '执行中', succeeded: '已完成', failed: '失败', retry_wait: '等待重试', cancelled: '已取消' };
const kinds: Record<string, string> = { 'file.scan': '文件扫描', 'file.derive': '预览转换', 'notification.dispatch': '通知投递', 'event.fanout': '事件分发' };
export function JobManagement() { return <ManagementGuard permission="audit.view"><Jobs /></ManagementGuard>; }
function Jobs() {
  const user = useAuthStore(s => s.user);
  const client = useQueryClient();
  const [status, setStatus] = useState('failed');
  const [kind, setKind] = useState('');
  const [target, setTarget] = useState<ManagedJob | null>(null);
  const query = useWorkspaceList<ManagedJob>('managed-jobs', '/admin/jobs', { ...(status ? { status } : {}), ...(kind ? { job_type: kind } : {}) });
  const rows = query.data?.pages.flatMap(page => page.items) ?? [];
  const retry = useMutation({
    mutationFn: () => { if (!target) throw new Error('请选择任务'); return api.post(`/admin/jobs/${target.id}/retry`); },
    onSuccess: () => { setTarget(null); client.invalidateQueries({ queryKey: ['managed-jobs'] }); client.invalidateQueries({ queryKey: ['ops-workbench'] }); toast.success('已重新排队，等待后台执行'); },
    onError: error => {
      const failure = describeError(error);
      toast.error(failure.message);
      if (failure.code === 'JOB_NOT_RETRYABLE') setTarget(null);
      query.refetch();
    },
  });
  return <section className="space-y-5"><header className="flex flex-wrap justify-between gap-3"><h2 className="dn-display text-2xl">异步任务</h2><Button variant="secondary" onClick={() => query.refetch()}>刷新状态</Button></header>
    <div className="grid gap-3 sm:grid-cols-2"><select aria-label="任务状态" className={fieldClass} value={status} onChange={e => setStatus(e.target.value)}><option value="">全部状态</option>{Object.entries(states).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><select aria-label="任务类型" className={fieldClass} value={kind} onChange={e => setKind(e.target.value)}><option value="">全部类型</option>{Object.entries(kinds).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div>
    <QueryFeedback pending={query.isPending} error={query.error} empty={!rows.length} retry={() => query.refetch()} />
    <ul className="space-y-3">{rows.map(row => <li key={row.id} className="space-y-3 border border-divider p-5"><div className="flex flex-wrap justify-between gap-3"><h3 className="font-semibold">{kinds[row.job_type] ?? row.job_type} · {states[row.status] ?? row.status}</h3>{hasPermission(user, 'job.retry') && ['failed', 'cancelled'].includes(row.status) && <Button variant="secondary" onClick={() => setTarget(row)}>重新排队</Button>}</div><p className="text-sm text-text-secondary">已尝试 {row.attempts}/{row.max_attempts} · 更新于 {formatDateTime(row.updated_at)}</p>{row.last_error && <p className="break-all border-l-2 border-crimson pl-3 text-sm">{row.last_error}</p>}<details className="text-sm"><summary className="cursor-pointer">任务详情</summary><dl className="mt-2 space-y-1 break-all text-text-secondary"><div>任务 ID：{row.id}</div><div>关联对象：{row.target_id}</div><div>失败分类：{row.failure_class ?? '—'}</div><div>下次执行：{formatDateTime(row.next_run_at)}</div></dl></details></li>)}</ul>
    {query.hasNextPage && <Button onClick={() => query.fetchNextPage()} isLoading={query.isFetchingNextPage}>更多任务</Button>}
    <Modal isOpen={!!target} onClose={() => { if (!retry.isPending) setTarget(null); }} title="重新排队任务"><div className="space-y-4"><p className="break-all">{target?.job_type} · {target?.id}</p><p className="text-sm text-text-secondary">将重置尝试次数并交给后台重新执行；排队成功不代表任务完成。</p><div className="flex gap-3"><Button onClick={() => retry.mutate()} isLoading={retry.isPending}>确认重新排队</Button><Button variant="secondary" disabled={retry.isPending} onClick={() => setTarget(null)}>取消</Button></div></div></Modal>
  </section>;
}
