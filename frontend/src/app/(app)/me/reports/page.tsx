'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { SkeletonList } from '@/components/ui/Skeleton';
import { describeError } from '@/lib/api/client';
import { listMyCommentAppeals, listMyReportEvents, listMyReports } from '@/lib/api/governance';
import { REPORT_REASON_LABELS, type ReportReasonCode } from '@/lib/api/moderation';
import type { ReportStatus } from '@/lib/api/types';
import { formatDateTime } from '@/lib/utils';
import { useAuthStore } from '@/stores/authStore';

const reportLabels: Record<ReportStatus, string> = {
  open: '待处理', triaged: '已受理', investigating: '调查中', resolved: '已处理', dismissed: '未采纳', appealed: '复核中',
};
const appealLabels = { pending: '待处理', accepted: '申诉通过', rejected: '申诉未通过' };

export default function MyReportsPage() {
  const [tab, setTab] = useState<'resource' | 'comment' | 'appeal'>('resource');
  return <div className="space-y-5">
    <h1 className="dn-display text-3xl">举报与申诉</h1>
    <div role="group" aria-label="记录类型" className="flex flex-wrap gap-2">
      {([['resource', '资料举报'], ['comment', '评论举报'], ['appeal', '评论申诉']] as const).map(([value, label]) => <Button key={value} variant={tab === value ? 'primary' : 'secondary'} aria-pressed={tab === value} onClick={() => setTab(value)}>{label}</Button>)}
    </div>
    {tab === 'appeal' ? <Appeals /> : <Reports key={tab} kind={tab} />}
  </div>;
}

function Reports({ kind }: { kind: 'resource' | 'comment' }) {
  const user = useAuthStore(s => s.user);
  const [status, setStatus] = useState<ReportStatus | ''>('');
  const query = useInfiniteQuery({
    queryKey: ['my-reports', user?.id, kind, status], enabled: !!user,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => listMyReports({ kind, status: status || undefined, cursor: pageParam }),
    getNextPageParam: page => page.has_more ? page.next_cursor : undefined,
  });
  const items = query.data?.pages.flatMap(page => page.items) ?? [];
  return <div className="space-y-4">
    <label className="flex items-center gap-3 text-sm">处理状态
      <select value={status} onChange={e => setStatus(e.target.value as ReportStatus | '')} className="min-h-11 border border-control-border bg-surface px-3">
        <option value="">全部</option>
        {Object.entries(reportLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
    </label>
    {query.error ? <Notice tone="warning" title="举报记录加载失败" action={<Button onClick={() => query.refetch()}>重试</Button>}>{describeError(query.error).message}</Notice>
      : query.isPending ? <SkeletonList count={3} /> : items.length === 0 ? <EmptyState title="暂无符合条件的举报记录" />
      : <ul className="space-y-3">{items.map(item => <li key={item.id} className="space-y-3 border border-divider bg-surface p-4">
        <div className="flex flex-wrap justify-between gap-2"><h2 className="font-medium">{item.title ?? '原内容当前不可访问'}</h2><span className="text-sm text-text-secondary">{reportLabels[item.status]}</span></div>
        <p className="text-sm">{REPORT_REASON_LABELS[item.reason_code as ReportReasonCode] ?? item.reason_code}</p>
        {item.description && <p className="whitespace-pre-wrap break-words text-sm text-text-secondary">{item.description}</p>}
        <p className="text-xs text-text-secondary">提交于 {formatDateTime(item.created_at)} · 更新于 {formatDateTime(item.updated_at)}</p>
        {item.available && item.resource_id && <Link href={`/resources/${item.resource_id}`} className="inline-flex min-h-11 items-center text-sm text-brand underline">查看原内容</Link>}
        <ReportHistory id={item.id} />
      </li>)}</ul>}
    {query.hasNextPage && <Button variant="secondary" onClick={() => query.fetchNextPage()} isLoading={query.isFetchingNextPage}>更多举报</Button>}
  </div>;
}

function ReportHistory({ id }: { id: string }) {
  const user = useAuthStore(s => s.user);
  const [open, setOpen] = useState(false);
  const query = useInfiniteQuery({
    queryKey: ['my-report-events', user?.id, id], enabled: open && !!user,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => listMyReportEvents(id, pageParam),
    getNextPageParam: page => page.has_more ? page.next_cursor : undefined,
  });
  const items = query.data?.pages.flatMap(page => page.items) ?? [];
  return <div>
    <Button variant="ghost" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? '收起处理进度' : '查看处理进度'}</Button>
    {open && (query.error ? <Notice tone="warning" title="处理进度加载失败" action={<Button onClick={() => query.refetch()}>重试</Button>}>{describeError(query.error).message}</Notice>
      : query.isPending ? <p role="status" className="text-sm">正在加载进度…</p>
      : items.length === 0 ? <p className="text-sm text-text-secondary">暂无状态流转记录。</p>
      : <ol className="mt-2 space-y-2 border-l border-divider pl-4 text-sm">{items.map(item => <li key={item.id}>{item.from_status ? `${reportLabels[item.from_status]} → ` : ''}{reportLabels[item.to_status]}<time className="ml-3 text-text-secondary">{formatDateTime(item.created_at)}</time></li>)}</ol>)}
    {open && query.hasNextPage && <Button variant="ghost" onClick={() => query.fetchNextPage()} isLoading={query.isFetchingNextPage}>更早进度</Button>}
  </div>;
}

function Appeals() {
  const user = useAuthStore(s => s.user);
  const query = useInfiniteQuery({
    queryKey: ['my-comment-appeals', user?.id], enabled: !!user,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => listMyCommentAppeals({ cursor: pageParam }),
    getNextPageParam: page => page.has_more ? page.next_cursor : undefined,
  });
  const items = query.data?.pages.flatMap(page => page.items) ?? [];
  return <div className="space-y-4">
    {query.error ? <Notice tone="warning" title="申诉记录加载失败" action={<Button onClick={() => query.refetch()}>重试</Button>}>{describeError(query.error).message}</Notice>
      : query.isPending ? <SkeletonList count={3} /> : items.length === 0 ? <EmptyState title="还没有评论申诉" description="被隐藏评论的申诉可在原资料讨论区提交。" />
      : <ul className="space-y-3">{items.map(item => <li key={item.id} className="space-y-3 border border-divider bg-surface p-4">
        <div className="flex flex-wrap justify-between gap-2"><h2 className="font-medium">{item.title ?? '原内容当前不可访问'}</h2><span className="text-sm text-text-secondary">{appealLabels[item.status]}</span></div>
        <p className="whitespace-pre-wrap break-words text-sm">{item.reason}</p>
        <p className="text-xs text-text-secondary">提交于 {formatDateTime(item.created_at)}{item.handled_at ? ` · 处理于 ${formatDateTime(item.handled_at)}` : ''}</p>
        {item.available && item.resource_id && <Link href={`/resources/${item.resource_id}`} className="inline-flex min-h-11 items-center text-sm text-brand underline">查看讨论与评论状态</Link>}
      </li>)}</ul>}
    {query.hasNextPage && <Button variant="secondary" onClick={() => query.fetchNextPage()} isLoading={query.isFetchingNextPage}>更多申诉</Button>}
  </div>;
}
