'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';

import { Notice } from '@/components/common/Notice';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { Modal } from '@/components/ui/Modal';
import { SkeletonList } from '@/components/ui/Skeleton';
import { ContributorStatusForm } from '@/components/workspace/ContributorStatusForm';
import { describeError } from '@/lib/api/client';
import {
  CONTRIBUTOR_DECISION_LABELS,
  CONTRIBUTOR_ONBOARDING_STATUS_LABELS,
  decideContributorApplication,
  listContributorApplications,
  type ContributorApplicationOut,
  type ContributorDecision,
  type ContributorOnboardingStatus,
} from '@/lib/api/creator';
import { formatDateTime } from '@/lib/utils';
import { hasPermission, useAuthStore } from '@/stores/authStore';

const statusTone: Record<ContributorOnboardingStatus, 'gray' | 'yellow' | 'green' | 'red' | 'orange'> = {
  applied: 'gray',
  under_review: 'yellow',
  active: 'green',
  rejected: 'red',
  suspended: 'orange',
  exited: 'gray',
};

const DECISIONS: ContributorDecision[] = ['under_review', 'active', 'rejected'];

export default function AdminContributorApplicationsPage() {
  const user = useAuthStore((s) => s.user);
  return hasPermission(user, 'contributor.review') ? <ApplicationsPanel /> : <Notice title="需要入驻审核权限" />;
}

function ApplicationsPanel() {
  const [status, setStatus] = useState<ContributorOnboardingStatus | ''>('');
  const [active, setActive] = useState<ContributorApplicationOut | null>(null);

  const listQuery = useQuery({
    queryKey: ['admin-contributor-applications', status],
    queryFn: () =>
      listContributorApplications({ status: status ? [status] : undefined }),
  });
  const items = listQuery.data?.items ?? [];

  return (
    <div className="space-y-6">
      <label className="flex items-center gap-3 text-sm">
        处理状态
        <select
          value={status}
          onChange={(event) => setStatus(event.target.value as ContributorOnboardingStatus | '')}
          className="min-h-11 border border-control-border bg-surface px-3"
        >
          <option value="">全部</option>
          {Object.entries(CONTRIBUTOR_ONBOARDING_STATUS_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>

      {listQuery.isLoading ? (
        <SkeletonList count={3} />
      ) : listQuery.error ? (
        <Notice
          tone="warning"
          title="入驻申请队列暂时无法加载"
          action={
            <Button variant="secondary" onClick={() => listQuery.refetch()}>
              重试
            </Button>
          }
        >
          {describeError(listQuery.error).message}
        </Notice>
      ) : items.length === 0 ? (
        <EmptyState title="当前没有入驻申请" description="用户提交入驻申请后会出现在这里。" />
      ) : (
        <ul className="space-y-3">
          {items.map((item) => (
            <li key={item.id} className="border border-divider bg-surface p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-xs text-text-secondary">
                    申请人 {item.user_id.slice(0, 8)}… · 提交于 {formatDateTime(item.submitted_at)}
                  </p>
                  <p className="mt-1 line-clamp-2 whitespace-pre-wrap break-words text-sm text-text-primary">
                    {item.capability_summary}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <Badge color={statusTone[item.status]}>
                    {CONTRIBUTOR_ONBOARDING_STATUS_LABELS[item.status]}
                  </Badge>
                  <Button variant="primary" onClick={() => setActive(item)}>
                    审核
                  </Button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}

      {active && (
        <DecisionModal
          application={active}
          onClose={() => setActive(null)}
          onChanged={() => listQuery.refetch()}
        />
      )}
    </div>
  );
}

function DecisionModal({
  application,
  onClose,
  onChanged,
}: {
  application: ContributorApplicationOut;
  onClose: () => void;
  onChanged: () => void;
}) {
  const queryClient = useQueryClient();
  const [decision, setDecision] = useState<ContributorDecision | ''>('');
  const [reason, setReason] = useState('');

  const mutation = useMutation({
    mutationFn: () =>
      decideContributorApplication(application.id, {
        decision: decision as ContributorDecision,
        reason: reason.trim(),
      }),
    onSuccess: () => {
      toast.success('入驻申请已处理');
      setDecision('');
      setReason('');
      queryClient.invalidateQueries({ queryKey: ['admin-contributor-applications'] });
      onChanged();
      onClose();
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const lifecycle = application.status === 'active' || application.status === 'suspended';
  const terminal = application.status === 'exited' || application.status === 'rejected';

  return (
    <Modal isOpen onClose={onClose} title={`入驻申请 · ${application.id.slice(0, 8)}…`}>
      <div className="space-y-5">
        <section className="space-y-2 text-sm">
          <p className="text-text-secondary">
            当前状态：
            {CONTRIBUTOR_ONBOARDING_STATUS_LABELS[application.status]} · 提交于{' '}
            {formatDateTime(application.submitted_at)}
          </p>
          <div>
            <p className="text-text-secondary">能力说明</p>
            <p className="whitespace-pre-wrap break-words text-text-primary">
              {application.capability_summary}
            </p>
          </div>
          <div>
            <p className="text-text-secondary">权利材料引用</p>
            <p className="break-words text-text-primary">{application.rights_materials_ref}</p>
          </div>
          {application.verification_ref && (
            <div>
              <p className="text-text-secondary">身份核验引用</p>
              <p className="break-words text-text-primary">{application.verification_ref}</p>
            </div>
          )}
          {application.contract_party_ref && (
            <div>
              <p className="text-text-secondary">签约主体引用</p>
              <p className="break-words text-text-primary">{application.contract_party_ref}</p>
            </div>
          )}
          {application.decision_reason && (
            <p className="whitespace-pre-wrap break-words text-text-secondary">
              上次结论：{application.decision_reason}
            </p>
          )}
        </section>

        {lifecycle ? (
          <ContributorStatusForm application={application} onChanged={() => {
            onChanged(); onClose();
          }} />
        ) : terminal ? (
          <Notice tone="info" title="该申请已终态">
            已退出或已拒绝的申请不再受理；如需入驻，请由用户重新提交。
          </Notice>
        ) : (
          <form
            className="space-y-3 border-t border-divider pt-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (!decision) {
                toast.error('请选择审核决定');
                return;
              }
              mutation.mutate();
            }}
          >
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium text-text-primary">审核决定</legend>
              {DECISIONS.map((value) => (
                <label
                  key={value}
                  className="flex min-h-11 items-center gap-2 text-sm text-text-primary"
                >
                  <input
                    type="radio"
                    name="contributor-decision"
                    value={value}
                    checked={decision === value}
                    onChange={() => setDecision(value)}
                  />
                  {CONTRIBUTOR_DECISION_LABELS[value]}
                </label>
              ))}
            </fieldset>
            <label className="block text-sm text-text-primary">
              理由（必填）
              <textarea
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                rows={3}
                maxLength={255}
                required
                className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3 py-2 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
                placeholder="请填写 1–255 字理由，会记录并通知申请人。"
              />
            </label>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="secondary" onClick={onClose}>
                关闭
              </Button>
              <Button type="submit" isLoading={mutation.isPending} disabled={!decision || !reason.trim()}>
                提交决定
              </Button>
            </div>
          </form>
        )}
      </div>
    </Modal>
  );
}
