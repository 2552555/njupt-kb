'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';

import { Notice } from '@/components/common/Notice';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { SkeletonList } from '@/components/ui/Skeleton';
import { describeError } from '@/lib/api/client';
import {
  CASE_STATUS_LABELS,
  CASE_TYPE_LABELS,
  CASE_TYPE_ORDER,
  listMyCases,
  type CaseStatus,
  type CaseType,
} from '@/lib/api/cases';
import { formatDateTime } from '@/lib/utils';
import { useAuthStore } from '@/stores/authStore';

const statusTone: Record<CaseStatus, 'gray' | 'yellow' | 'blue' | 'green' | 'orange'> = {
  open: 'yellow',
  triaged: 'blue',
  investigating: 'blue',
  decided: 'green',
  closed: 'gray',
};

const selectClass = 'min-h-11 border border-control-border bg-surface px-3';

export default function MyCasesPage() {
  const user = useAuthStore((s) => s.user);
  const [caseType, setCaseType] = useState<CaseType | ''>('');
  const [state, setState] = useState<CaseStatus | ''>('');

  const query = useInfiniteQuery({
    queryKey: ['my-cases', user?.id, caseType, state],
    enabled: !!user,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      listMyCases({
        case_type: caseType ? [caseType] : undefined,
        state: state ? [state] : undefined,
        cursor: pageParam,
      }),
    getNextPageParam: (page) => (page.has_more ? page.next_cursor : undefined),
  });
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <div className="space-y-6">
      <header>
        <p className="dn-kicker text-text-secondary">CASES</p>
        <h1 className="dn-display mt-1 text-3xl">我的案件</h1>
      </header>

      <div className="flex flex-wrap items-center gap-4">
        <label className="flex items-center gap-3 text-sm">
          案件类型
          <select
            value={caseType}
            onChange={(event) => setCaseType(event.target.value as CaseType | '')}
            className={selectClass}
          >
            <option value="">全部</option>
            {CASE_TYPE_ORDER.map((value) => (
              <option key={value} value={value}>
                {CASE_TYPE_LABELS[value]}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-3 text-sm">
          处理状态
          <select
            value={state}
            onChange={(event) => setState(event.target.value as CaseStatus | '')}
            className={selectClass}
          >
            <option value="">全部</option>
            {Object.entries(CASE_STATUS_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {query.error ? (
        <Notice
          tone="warning"
          title="案件记录暂时无法加载"
          action={
            <Button variant="secondary" onClick={() => query.refetch()}>
              重试
            </Button>
          }
        >
          {describeError(query.error).message}
        </Notice>
      ) : query.isPending ? (
        <SkeletonList count={3} />
      ) : items.length === 0 ? (
        <EmptyState title="暂无符合条件的案件" description="提交侵权通知或申诉后会在这里出现。" />
      ) : (
        <ul className="space-y-3">
          {items.map((item) => (
            <li key={item.id} className="border border-divider bg-surface p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-medium text-text-primary">{item.title}</p>
                  <p className="mt-1 text-xs text-text-secondary">
                    {CASE_TYPE_LABELS[item.case_type]} · 更新于 {formatDateTime(item.updated_at)}
                  </p>
                </div>
                <Badge color={statusTone[item.state]}>{CASE_STATUS_LABELS[item.state]}</Badge>
              </div>
              <Link
                href={`/me/cases/${item.id}`}
                className="mt-2 inline-flex min-h-11 items-center text-sm text-brand underline"
              >
                查看详情
              </Link>
            </li>
          ))}
        </ul>
      )}

      {query.hasNextPage && (
        <Button
          variant="secondary"
          isLoading={query.isFetchingNextPage}
          onClick={() => query.fetchNextPage()}
        >
          更多案件
        </Button>
      )}
    </div>
  );
}
