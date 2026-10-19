'use client';
import { useState } from 'react';
import { useInfiniteQuery, useMutation, useQuery } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { Notice } from '@/components/common/Notice';
import { fieldClass, ManagementGuard, QueryFeedback } from '@/components/workspace/Workspace';
import { fetchFeatures } from '@/lib/api/features';
import { createPayoutAttempt, listPayoutAttempts, listPayouts, reconcilePayoutAttempt, type Payout, type PayoutAttempt } from '@/lib/api/settlement';
import { describeError } from '@/lib/api/client';

const labels: Record<string, string> = { created: '待处理', processing: '处理中', succeeded: '已核实成功', failed: '已核实失败', uncertain: '结果不确定，继续占款' };

export default function PayoutsPage() { return <ManagementGuard permission="payout.manage"><Payouts /></ManagementGuard>; }

function Payouts() {
  const [target, setTarget] = useState<Payout | null>(null);
  const flags = useQuery({ queryKey: ['features'], queryFn: fetchFeatures });
  const query = useInfiniteQuery({
    queryKey: ['admin-payouts'], initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => listPayouts(pageParam), getNextPageParam: page => page.has_more ? page.next_cursor : undefined,
  });
  const rows = query.data?.pages.flatMap(page => page.items) ?? [];
  return <section className="space-y-4">
    <h2 className="dn-display text-2xl">付款登记与核对</h2>
    <Notice title="外部付款须单独执行并核验">此页面登记付款尝试与核对证据，不会发起银行或支付渠道转账。不确定结果继续占款，查明前不能新建付款尝试。</Notice>
    <QueryFeedback pending={query.isPending} error={query.error} empty={!rows.length} retry={() => query.refetch()} emptyText="暂无付款计划" />
    <ul className="space-y-3">{rows.map(row => <li className="space-y-2 border border-divider p-4" key={row.id}>
      <p className="break-all text-sm">结算单 {row.statement_version_id}</p>
      <p>金额 {(row.amount_fen / 100).toFixed(2)} · {labels[row.state] ?? row.state}</p>
      <Button variant="secondary" onClick={() => setTarget(row)}>查看付款尝试与核对</Button>
    </li>)}</ul>
    {query.hasNextPage && <Button onClick={() => query.fetchNextPage()} isLoading={query.isFetchingNextPage}>更多付款计划</Button>}
    {target && <AttemptModal payout={target} enabled={flags.data?.commerce_enabled === true}
      onClose={() => setTarget(null)} onChanged={() => { query.refetch(); setTarget(null); }} />}
  </section>;
}

function AttemptModal({ payout, enabled, onClose, onChanged }: { payout: Payout; enabled: boolean; onClose: () => void; onChanged: () => void }) {
  const query = useInfiniteQuery({
    queryKey: ['payout-attempts', payout.id], initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => listPayoutAttempts(payout.id, pageParam), getNextPageParam: page => page.has_more ? page.next_cursor : undefined,
  });
  const create = useMutation({
    mutationFn: () => createPayoutAttempt(payout.id), onSuccess: () => { toast.success('付款尝试已登记，请核对真实外部结果'); onChanged(); },
    onError: error => toast.error(describeError(error).message),
  });
  const rows = query.data?.pages.flatMap(page => page.items) ?? [];
  return <Modal isOpen title="付款尝试与核对" onClose={onClose}>
    <div className="space-y-4">
      {!enabled && <Notice title="付款写操作未启用" />}
      <QueryFeedback pending={query.isPending} error={query.error} empty={!rows.length} retry={() => query.refetch()} emptyText="尚未登记付款尝试" />
      {rows.map(row => <AttemptForm key={row.id} attempt={row} enabled={enabled} onChanged={onChanged} />)}
      {query.hasNextPage && <Button onClick={() => query.fetchNextPage()} isLoading={query.isFetchingNextPage}>更多历史尝试</Button>}
      {enabled && ['created', 'failed'].includes(payout.state) && <Button isLoading={create.isPending} onClick={() => create.mutate()}>登记并预留新付款尝试</Button>}
    </div>
  </Modal>;
}

function AttemptForm({ attempt, enabled, onChanged }: { attempt: PayoutAttempt; enabled: boolean; onChanged: () => void }) {
  const [result, setResult] = useState<'succeeded' | 'failed' | 'uncertain'>('uncertain');
  const [evidence, setEvidence] = useState('');
  const [reference, setReference] = useState('');
  const [reason, setReason] = useState('');
  const [checked, setChecked] = useState(false);
  const mutation = useMutation({
    mutationFn: () => reconcilePayoutAttempt(attempt.id, { result, evidence_ref: evidence.trim(), external_ref_key: reference.trim() || undefined, reason: reason.trim() }),
    onSuccess: () => { toast.success('核对结果已记录'); onChanged(); }, onError: error => toast.error(describeError(error).message),
  });
  return <div className="space-y-3 border border-divider p-3">
    <p>第 {attempt.attempt_no} 次 · {labels[attempt.state] ?? attempt.state}</p>
    {attempt.evidence_ref && <p className="break-all text-sm">证据引用 {attempt.evidence_ref}</p>}
    {enabled && ['created', 'processing', 'uncertain'].includes(attempt.state) && <form className="space-y-3" onSubmit={event => { event.preventDefault(); mutation.mutate(); }}>
      <label className="block text-sm">核对结果<select className={fieldClass} value={result} onChange={event => setResult(event.target.value as typeof result)}>
        <option value="uncertain">不确定，继续占款</option><option value="succeeded">已核实成功</option><option value="failed">已核实未支付</option>
      </select></label>
      <label className="block text-sm">真实证据引用<input className={fieldClass} required maxLength={512} value={evidence} onChange={event => setEvidence(event.target.value)} /></label>
      {result === 'succeeded' && <label className="block text-sm">真实转出唯一键<input className={fieldClass} required maxLength={200} value={reference} onChange={event => setReference(event.target.value)} /></label>}
      <label className="block text-sm">核对说明<textarea className={fieldClass} required maxLength={2000} value={reason} onChange={event => setReason(event.target.value)} /></label>
      <label className="flex min-h-11 items-center gap-2 text-sm"><input type="checkbox" checked={checked} onChange={event => setChecked(event.target.checked)} />已核对外部真实记录，理解本操作不发起转账</label>
      <Button type="submit" disabled={!checked} isLoading={mutation.isPending}>记录核对结果</Button>
    </form>}
  </div>;
}
