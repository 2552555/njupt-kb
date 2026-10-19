'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { SkeletonList } from '@/components/ui/Skeleton';
import { describeError } from '@/lib/api/client';
import { listPendingGuides, reviewGuide, type GuidePendingItem } from '@/lib/api/guides';
import { formatDateTime } from '@/lib/utils';

export default function AdminGuidesPage() {
  const queryClient = useQueryClient();
  const [notes, setNotes] = useState<Record<string, string>>({});
  const query = useQuery({ queryKey: ['admin-guides-pending'], queryFn: listPendingGuides });

  const reviewMutation = useMutation({
    mutationFn: ({ item, approve }: { item: GuidePendingItem; approve: boolean }) =>
      reviewGuide(item.article_id, {
        version_no: item.version_no,
        approve,
        note: notes[item.article_id]?.trim() || undefined,
      }),
    onSuccess: (_, { approve }) => {
      toast.success(approve ? '已通过并发布' : '已驳回');
      queryClient.invalidateQueries({ queryKey: ['admin-guides-pending'] });
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const items = query.data?.items ?? [];

  return (
    <div className="space-y-6">
      <header className="border-b border-divider pb-6">
        <h1 className="dn-display text-3xl">攻略审核</h1>
        <p className="mt-3 text-text-secondary">
          只审核待审版本；通过后该版本成为公开正文，驳回时保留原有公开版本。审核备注会通知作者。
        </p>
      </header>

      {query.isLoading ? (
        <SkeletonList count={4} />
      ) : query.isError ? (
        <Notice tone="error" title="无法加载审核队列">{describeError(query.error).message}</Notice>
      ) : items.length === 0 ? (
        <EmptyState title="没有待审攻略" description="作者提交后会出现在这里。" />
      ) : (
        <ul className="space-y-4">
          {items.map((item) => (
            <li key={`${item.article_id}-${item.version_no}`} className="border border-divider bg-surface p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="text-base font-medium">{item.title}</h2>
                  <p className="mt-1 text-sm text-text-secondary">
                    第 {item.version_no} 版 · 提交于 {item.submitted_at ? formatDateTime(item.submitted_at) : '未知时间'}
                  </p>
                  {item.summary && <p className="mt-2 text-sm">{item.summary}</p>}
                </div>
                <Link
                  href={`/guides/${item.article_id}`}
                  className="inline-flex min-h-11 items-center border border-control-border px-3 text-sm"
                >
                  查看正文
                </Link>
              </div>
              <label htmlFor={`note-${item.article_id}`} className="mt-3 block text-sm font-medium">
                审核备注（驳回时建议填写）
              </label>
              <textarea
                id={`note-${item.article_id}`}
                rows={2}
                value={notes[item.article_id] ?? ''}
                onChange={(event) =>
                  setNotes((prev) => ({ ...prev, [item.article_id]: event.target.value }))
                }
                className="mt-1 w-full border border-control-border bg-surface px-3 py-2 text-sm"
                placeholder="例如：缺少资料来源，请补充适用学期与教师信息"
              />
              <div className="mt-3 flex flex-wrap gap-2">
                <Button
                  onClick={() => reviewMutation.mutate({ item, approve: true })}
                  isLoading={reviewMutation.isPending}
                >
                  通过并发布
                </Button>
                <Button
                  variant="danger"
                  onClick={() => reviewMutation.mutate({ item, approve: false })}
                  isLoading={reviewMutation.isPending}
                >
                  驳回
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <Notice tone="info" title="边界">
        审核只决定该版本是否公开，不代表内容事实已被核实；作者与读者仍需自行核对适用学期与教师。
      </Notice>
    </div>
  );
}
