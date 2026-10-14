'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { SkeletonList } from '@/components/ui/Skeleton';
import { ApiError, describeError } from '@/lib/api/client';
import { moderationSummary } from '@/lib/api/admin';
import { decideReview, listReviews, type ReviewOut } from '@/lib/api/moderation';
import type { ModerationDecisionType } from '@/lib/api/types';
import { formatDateTime } from '@/lib/utils';

export default function AdminReviewsPage() {
  const queryClient = useQueryClient();
  const [active, setActive] = useState<ReviewOut | null>(null);
  const [reason, setReason] = useState('');
  const [confirmReject, setConfirmReject] = useState(false);

  const listQuery = useQuery({
    queryKey: ['admin-reviews', 'pending'],
    queryFn: () => listReviews({ status: ['pending'], limit: 20 }),
  });

  const summaryQuery = useQuery({
    queryKey: ['admin-moderation-summary'],
    queryFn: () => moderationSummary(),
  });

  const decisionMutation = useMutation({
    mutationFn: ({ decision }: { decision: ModerationDecisionType }) => {
      if (!active) throw new Error('未选择审核任务');
      return decideReview(active.id, {
        decision,
        reason: reason.trim(),
        snapshot_revision: active.snapshot_revision,
      });
    },
    onSuccess: (_result, variables) => {
      toast.success(variables.decision === 'approve' ? '已批准' : '已驳回');
      setActive(null);
      setReason('');
      setConfirmReject(false);
      queryClient.invalidateQueries({ queryKey: ['admin-reviews'] });
      queryClient.invalidateQueries({ queryKey: ['admin-moderation-summary'] });
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        toast.error('该审核任务的状态或快照已变化，请刷新后重新查看。');
        queryClient.invalidateQueries({ queryKey: ['admin-reviews'] });
        return;
      }
      toast.error(describeError(error).message);
    },
  });

  const pendingCount = listQuery.data?.items.length ?? 0;

  return (
    <div className="space-y-6">
      {summaryQuery.data && (
        <div className="grid gap-4 sm:grid-cols-3">
          <SummaryTile
            label="待审版本"
            value={String(summaryQuery.data.reviews.pending ?? 0)}
          />
          <SummaryTile
            label="待处理举报"
            value={String(summaryQuery.data.reports.open ?? 0)}
          />
          <SummaryTile
            label="最早提交时间"
            value={
              summaryQuery.data.oldest_pending_submitted_at
                ? formatDateTime(summaryQuery.data.oldest_pending_submitted_at)
                : '暂无'
            }
          />
        </div>
      )}

      {summaryQuery.error && (
        <Notice tone="info" title="审核概览暂时不可用">
          {describeError(summaryQuery.error).message}
        </Notice>
      )}

      {listQuery.isLoading ? (
        <SkeletonList count={3} />
      ) : listQuery.error ? (
        <Notice
          tone="warning"
          title="审核队列暂时无法加载"
          action={<Button variant="secondary" onClick={() => listQuery.refetch()}>重试</Button>}
        >
          {describeError(listQuery.error).message}
        </Notice>
      ) : pendingCount > 0 ? (
        <ul className="space-y-3">
          {listQuery.data?.items.map((review) => (
            <li key={review.id} className="border border-divider bg-surface p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-medium text-text-primary">
                    版本快照 r{review.snapshot_revision}
                    <span className="ml-2 bg-amber px-2 py-0.5 text-xs font-semibold text-on-color">
                      待审核
                    </span>
                  </p>
                  <p className="mt-1 text-xs text-text-secondary">
                    资源 {review.resource_id.slice(0, 8)}… · 版本 {review.version_id.slice(0, 8)}…
                    · 提交于 {formatDateTime(review.submitted_at)}
                  </p>
                  <div className="mt-2 flex flex-wrap gap-3 text-xs">
                    <Link
                      href={`/resources/${review.resource_id}`}
                      className="underline underline-offset-2 text-text-secondary hover:text-text-primary"
                    >
                      查看原件与预览
                    </Link>
                  </div>
                  <p className="mt-2 text-xs text-text-secondary">
                    风险提示：后端未提供结构化风险提示接口，审核员需自行查看文件与来源后判断。
                  </p>
                </div>
                <Button
                  variant="primary"
                  disabled={review.can_decide === false}
                  onClick={() => {
                    setActive(review);
                    setReason('');
                    setConfirmReject(false);
                  }}
                >
                  {review.can_decide === false ? '不能审核自己的提交' : '审核'}
                </Button>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <Notice tone="info" title="当前没有待审版本">
          新的提交会出现在这里；审核决定针对提交时的快照。
        </Notice>
      )}

      <Modal
        isOpen={!!active}
        onClose={() => setActive(null)}
        title={active ? `审核决定 · 快照 r${active.snapshot_revision}` : '审核决定'}
      >
        <form
          className="space-y-4"
          onSubmit={(event) => event.preventDefault()}
        >
          <p className="text-sm text-text-secondary">
            请先查看原件与来源，再给出明确结论与理由。理由会记录并通知作者。
          </p>
          <div>
            <label htmlFor="decision-reason" className="mb-1 block text-sm font-medium text-text-primary">
              审核理由（必填）
            </label>
            <textarea
              id="decision-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              rows={4}
              maxLength={2000}
              className="w-full border border-control-border bg-surface px-3 py-2 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
              placeholder="说明判断依据，例如内容适用性与来源是否允许分发。"
            />
          </div>
          {confirmReject && (
            <Notice tone="warning" title="确认驳回？">
              驳回会通知作者并保留该快照作为审核证据；已发布的旧版本不受影响。
              决定不可原地覆盖，作者修订后会形成新的提交。
            </Notice>
          )}

          <div className="flex justify-end gap-2">
            {confirmReject ? (
              <>
                <Button variant="ghost" onClick={() => setConfirmReject(false)}>
                  取消
                </Button>
                <Button
                  variant="danger"
                  onClick={() => decisionMutation.mutate({ decision: 'reject' })}
                  isLoading={decisionMutation.isPending}
                  disabled={!reason.trim()}
                >
                  确认驳回
                </Button>
              </>
            ) : (
              <>
                <Button
                  variant="danger"
                  onClick={() => setConfirmReject(true)}
                  disabled={!reason.trim()}
                >
                  驳回
                </Button>
                <Button
                  variant="primary"
                  onClick={() => decisionMutation.mutate({ decision: 'approve' })}
                  isLoading={decisionMutation.isPending}
                  disabled={!reason.trim()}
                >
                  批准
                </Button>
              </>
            )}
          </div>
        </form>
      </Modal>
    </div>
  );
}

function SummaryTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-ink px-4 py-5 text-on-ink">
      <p className="dn-kicker text-on-ink">{label}</p>
      <p className="dn-display mt-1 text-2xl">{value}</p>
    </div>
  );
}
