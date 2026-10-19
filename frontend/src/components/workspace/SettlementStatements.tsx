'use client';
import { useState } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { describeError } from '@/lib/api/client';
import { fetchFeatures } from '@/lib/api/features';
import { approveStatement, confirmStatement, disputeStatement, generateStatement, listStatements, type SettlementStatement } from '@/lib/api/settlement';
import { useAuthStore } from '@/stores/authStore';
import { fieldClass, QueryFeedback } from './Workspace';

const labels: Record<string, string> = {
  draft: '草稿', awaiting_confirmation: '待确认', confirmed: '已确认', approved: '已批准',
  payment_pending: '待付款', paid: '已付款', disputed: '有异议',
};
const money = (fen: number, currency: string) => `${currency} ${(fen / 100).toFixed(2)}`;

export function SettlementStatements({ admin = false }: { admin?: boolean }) {
  const user = useAuthStore(s => s.user);
  const flags = useQuery({ queryKey: ['features'], queryFn: fetchFeatures });
  const query = useInfiniteQuery({
    queryKey: ['settlement-statements', admin, user?.id], initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => listStatements(admin, pageParam),
    getNextPageParam: page => page.has_more ? page.next_cursor : undefined,
  });
  const items = query.data?.pages.flatMap(page => page.items) ?? [];
  const enabled = flags.data?.commerce_enabled === true;
  return <section className="space-y-4">
    <h2 className="dn-display text-2xl">{admin ? '结算管理' : '我的结算单'}</h2>
    {!enabled && <Notice title="结算写操作未启用">可查询历史记录；规则与准入条件确认前不能生成、确认或付款。</Notice>}
    {admin && enabled && <GenerateStatement />}
    <QueryFeedback pending={query.isPending} error={query.error} empty={!items.length} retry={() => query.refetch()} emptyText="暂无结算单" />
    <ul className="space-y-4">{items.map(statement => <StatementItem key={statement.id} statement={statement} enabled={enabled} admin={admin} />)}</ul>
    {query.hasNextPage && <Button variant="secondary" isLoading={query.isFetchingNextPage} onClick={() => query.fetchNextPage()}>更多结算单</Button>}
  </section>;
}

function StatementItem({ statement, enabled, admin }: { statement: SettlementStatement; enabled: boolean; admin: boolean }) {
  const client = useQueryClient();
  const [reason, setReason] = useState('');
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  const [checked, setChecked] = useState(false);
  const [account, setAccount] = useState('');
  const items = statement.items ?? [];
  const mutation = useMutation({
    mutationFn: (action: 'confirm' | 'dispute' | 'approve') => action === 'approve' ? approveStatement(statement, account.trim())
      : action === 'confirm' ? confirmStatement(statement) : disputeStatement(statement, reason.trim(),
        items.filter(item => Number(amounts[item.allocation_id]) > 0).map(item => ({
          allocation_id: item.allocation_id, amount_fen: Number(amounts[item.allocation_id]),
        }))),
    onSuccess: () => { toast.success('结算单已处理'); client.invalidateQueries({ queryKey: ['settlement-statements'] }); },
    onError: error => toast.error(describeError(error).message),
  });
  const canConfirm = ['awaiting_confirmation', 'confirmed'].includes(statement.state);
  const canApprove = ['awaiting_confirmation', 'confirmed'].includes(statement.state);
  const disputedItems = items.filter(item => Number(amounts[item.allocation_id]) > 0);
  const validDispute = reason.trim() && disputedItems.length > 0 && disputedItems.every(item =>
    Number.isInteger(Number(amounts[item.allocation_id])) && Number(amounts[item.allocation_id]) <= item.amount_fen);
  return <li className="space-y-3 border border-divider bg-surface p-4">
    <p>{statement.period} · v{statement.version} · {labels[statement.state] ?? statement.state}</p>
    <p className="text-sm">应付 {money(statement.gross_fen, statement.currency)} · 税款 {money(statement.tax_fen, statement.currency)} · 净额 {money(statement.net_fen, statement.currency)}</p>
    {admin ? <p className="break-all text-xs text-text-secondary">收款主体 {statement.payee_party_id}</p>
      : <p className="text-sm">收款账户 {statement.masked_account ?? '尚未登记'} · 付款状态 {statement.payout_state ?? '尚未生成付款计划'}</p>}
    {!!items.length && <ul className="space-y-2">{items.map(item => <li className="flex flex-wrap items-center gap-2 text-sm" key={item.id}>
      <span className="break-all">明细 {item.allocation_id} · {money(item.amount_fen, statement.currency)}</span>
      {enabled && !admin && canConfirm && <label>异议金额（分）
        <input className="ml-2 w-28 border border-control-border bg-surface p-2" type="number" min="0" max={item.amount_fen} step="1"
          value={amounts[item.allocation_id] ?? ''} onChange={event => setAmounts({ ...amounts, [item.allocation_id]: event.target.value })} />
      </label>}
    </li>)}</ul>}
    {enabled && <fieldset disabled={mutation.isPending} className="space-y-3">
      {(admin ? canApprove : canConfirm) && <>
        {admin && <label className="block text-sm">已核验的收款账户<input className={fieldClass} value={account} autoComplete="off" onChange={event => setAccount(event.target.value)} /></label>}
        <label className="flex min-h-11 items-center gap-2 text-sm"><input type="checkbox" checked={checked} onChange={event => setChecked(event.target.checked)} />已核对本版本结算明细</label>
        <Button disabled={!checked || (admin && !account.trim())} onClick={() => mutation.mutate(admin ? 'approve' : 'confirm')}>{admin ? '批准并锁定批次' : '确认结算单'}</Button>
      </>}
      {!admin && canConfirm && <>
        <label className="block text-sm">异议理由<textarea className={fieldClass} maxLength={2000} value={reason} onChange={event => setReason(event.target.value)} /></label>
        <Button variant="secondary" disabled={!validDispute} onClick={() => mutation.mutate('dispute')}>提交所选明细异议</Button>
      </>}
    </fieldset>}
  </li>;
}

function GenerateStatement() {
  const client = useQueryClient();
  const [payee, setPayee] = useState('');
  const [period, setPeriod] = useState('');
  const [currency, setCurrency] = useState('CNY');
  const mutation = useMutation({
    mutationFn: () => generateStatement(payee.trim(), period.trim(), currency),
    onSuccess: () => { toast.success('结算草稿已生成'); client.invalidateQueries({ queryKey: ['settlement-statements'] }); },
    onError: error => toast.error(describeError(error).message),
  });
  return <form className="space-y-3 border border-divider p-4" onSubmit={event => { event.preventDefault(); mutation.mutate(); }}>
    <h3 className="font-medium">生成结算草稿</h3>
    <label className="block text-sm">收款主体 ID<input className={fieldClass} required value={payee} onChange={event => setPayee(event.target.value)} /></label>
    <label className="block text-sm">结算期间<input className={fieldClass} required maxLength={32} value={period} onChange={event => setPeriod(event.target.value)} /></label>
    <label className="block text-sm">币种<input className={fieldClass} required pattern="[A-Z]{3}" value={currency} onChange={event => setCurrency(event.target.value.toUpperCase())} /></label>
    <Button type="submit" isLoading={mutation.isPending}>生成草稿</Button>
  </form>;
}
