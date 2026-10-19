'use client';

import { useState } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Button } from '@/components/ui/Button';
import { Notice } from '@/components/common/Notice';
import { QueryFeedback } from './Workspace';
import { fetchFeatures } from '@/lib/api/features';
import { confirmPrice, listPriceProposals, rejectPrice, type PriceProposalOut } from '@/lib/api/licensing';
import { describeError } from '@/lib/api/client';
import { useAuthStore } from '@/stores/authStore';

const states = { proposed: '待协商', negotiating: '协商中', confirmed: '双方已确认', published: '已发布', withdrawn: '已否决或撤回' };

export function PriceProposals({ admin = false }: { admin?: boolean }) {
  const user = useAuthStore(s => s.user);
  const features = useQuery({ queryKey: ['features'], queryFn: fetchFeatures });
  const query = useInfiniteQuery({
    queryKey: ['price-proposals', admin, user?.id], initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => listPriceProposals(admin, pageParam),
    getNextPageParam: page => page.has_more ? page.next_cursor : undefined,
  });
  const items = query.data?.pages.flatMap(page => page.items) ?? [];
  return <section className="space-y-4" aria-label="价格方案">
    <h2 className="dn-display text-2xl">价格方案</h2>
    {!features.data?.commerce_enabled && <Notice title="商业化未启用">可查看历史方案，当前不能确认或否决价格。</Notice>}
    <QueryFeedback pending={query.isPending} error={query.error} empty={!items.length}
      retry={() => query.refetch()} emptyText="暂无价格方案" />
    <ul className="space-y-3">{items.map(item => <PriceItem key={item.id} proposal={item} admin={admin}
      enabled={features.data?.commerce_enabled === true} />)}</ul>
    {query.hasNextPage && <Button variant="secondary" onClick={() => query.fetchNextPage()}
      isLoading={query.isFetchingNextPage}>更多方案</Button>}
  </section>;
}

function PriceItem({ proposal, admin, enabled }: { proposal: PriceProposalOut; admin: boolean; enabled: boolean }) {
  const client = useQueryClient();
  const [checked, setChecked] = useState(false);
  const [reason, setReason] = useState('');
  const mutation = useMutation({
    mutationFn: (action: 'confirm' | 'reject') => action === 'confirm'
      ? confirmPrice(proposal, admin) : rejectPrice(proposal.id, reason.trim()),
    onSuccess: () => {
      toast.success('价格方案已处理');
      client.invalidateQueries({ queryKey: ['price-proposals'] });
    },
    onError: error => toast.error(describeError(error).message),
  });
  const open = proposal.state === 'proposed' || proposal.state === 'negotiating';
  return <li className="space-y-3 border border-divider bg-surface p-4">
    <p className="break-all text-sm">商品 {proposal.product_id} · v{proposal.version_no} · {states[proposal.state]}</p>
    <p>建议售价 ¥{(proposal.suggested_fen / 100).toFixed(2)}{proposal.final_fen !== null && ` · 确认售价 ¥${(proposal.final_fen / 100).toFixed(2)}`}</p>
    <p className="break-all text-xs text-text-secondary">方案哈希 {proposal.proposal_hash}</p>
    {open && enabled && <fieldset className="space-y-3" disabled={mutation.isPending}>
      <label className="flex min-h-11 items-center gap-2 text-sm"><input type="checkbox" checked={checked}
        onChange={event => setChecked(event.target.checked)} />已核对本版本价格与对应授权条款</label>
      <Button disabled={!checked} onClick={() => mutation.mutate('confirm')}>确认此版本</Button>
      <label className="block text-sm">否决理由<textarea className="mt-1 w-full border border-control-border bg-surface p-2"
        maxLength={500} value={reason} onChange={event => setReason(event.target.value)} /></label>
      <Button variant="secondary" disabled={!reason.trim()} onClick={() => mutation.mutate('reject')}>否决此版本</Button>
    </fieldset>}
  </li>;
}
