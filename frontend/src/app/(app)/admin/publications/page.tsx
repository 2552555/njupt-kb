'use client';
import Link from 'next/link';
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { fieldClass, ManagementGuard, QueryFeedback, useWorkspaceList } from '@/components/workspace/Workspace';
import { setPublication } from '@/lib/api/admin';
import { describeError } from '@/lib/api/client';
import { formatDateTime } from '@/lib/utils';

interface Candidate { review_id: string; resource_id: string; version_id: string; title: string; expected_revision: number; reviewed_at: string | null }

export default function PublicationsPage() {
  return <ManagementGuard permission="resource.publish"><PublicationQueue /></ManagementGuard>;
}

function PublicationQueue() {
  const client = useQueryClient();
  const query = useWorkspaceList<Candidate>('publication-candidates', '/admin/publication-candidates');
  const rows = query.data?.pages.flatMap(page => page.items) ?? [];
  const [target, setTarget] = useState<Candidate | null>(null);
  const [reason, setReason] = useState('');
  const [checked, setChecked] = useState(false);
  const mutation = useMutation({
    mutationFn: async () => {
      if (!target) return;
      await setPublication(target.resource_id, {
        action: 'publish', version_id: target.version_id, expected_revision: target.expected_revision, reason: reason.trim(),
      });
    },
    onSuccess: () => { toast.success('资料已发布'); setTarget(null); client.invalidateQueries({ queryKey: ['publication-candidates'] }); },
    onError: error => { toast.error(describeError(error).message); client.invalidateQueries({ queryKey: ['publication-candidates'] }); },
  });
  return <section className="space-y-4">
    <h2 className="dn-display text-2xl">审核通过，等待发布</h2>
    <p className="text-sm text-text-secondary">只列每份资料最新通过审核的版本。发布时再次检查扫描与许可；审核通过本身不会替换公开版本。</p>
    <QueryFeedback pending={query.isPending} error={query.error} empty={!rows.length} retry={() => query.refetch()} emptyText="暂无待发布版本" />
    <ul className="space-y-3">{rows.map(row => <li key={row.review_id} className="space-y-3 border border-divider bg-surface p-4">
      <h3 className="font-medium">{row.title}</h3>
      <p className="text-sm text-text-secondary">审核于 {row.reviewed_at ? formatDateTime(row.reviewed_at) : '未记录时间'}</p>
      <div className="flex flex-wrap items-center gap-4">
        <Link className="text-sm underline" href={`/me/resources/${row.resource_id}/versions/${row.version_id}`}>核对版本、来源与文件</Link>
        <Button onClick={() => { setTarget(row); setReason(''); setChecked(false); }}>发布此版本</Button>
      </div>
    </li>)}</ul>
    {query.hasNextPage && <Button variant="secondary" isLoading={query.isFetchingNextPage} onClick={() => query.fetchNextPage()}>更多待发布版本</Button>}
    <Modal isOpen={!!target} title="发布审核通过的版本" onClose={() => setTarget(null)}>
      <form className="space-y-4" onSubmit={event => { event.preventDefault(); mutation.mutate(); }}>
        <p>{target?.title}</p>
        <label className="block text-sm">发布说明<textarea className={fieldClass} required maxLength={1000} value={reason} onChange={event => setReason(event.target.value)} /></label>
        <label className="flex min-h-11 items-center gap-2 text-sm"><input type="checkbox" checked={checked} onChange={event => setChecked(event.target.checked)} />已核对展示及传播许可与发布版本</label>
        <Button type="submit" disabled={!checked || !reason.trim()} isLoading={mutation.isPending}>确认发布</Button>
      </form>
    </Modal>
  </section>;
}
