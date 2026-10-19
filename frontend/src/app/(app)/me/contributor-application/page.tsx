'use client';

import { useState } from 'react';
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';

import { Notice } from '@/components/common/Notice';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { Input } from '@/components/ui/Input';
import { SkeletonList } from '@/components/ui/Skeleton';
import { ContributorStatusForm } from '@/components/workspace/ContributorStatusForm';
import { describeError } from '@/lib/api/client';
import {
  CONTRIBUTOR_ONBOARDING_STATUS_LABELS,
  listMyContributorApplications,
  submitContributorApplication,
  type ContributorApplicationOut,
  type ContributorOnboardingStatus,
} from '@/lib/api/creator';
import { formatDateTime } from '@/lib/utils';

const statusTone: Record<ContributorOnboardingStatus, 'gray' | 'yellow' | 'green' | 'red' | 'orange'> = {
  applied: 'gray',
  under_review: 'yellow',
  active: 'green',
  rejected: 'red',
  suspended: 'orange',
  exited: 'gray',
};

const textareaClass =
  'mt-1 min-h-11 w-full border border-control-border bg-surface px-3 py-2 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus';

export default function ContributorApplicationPage() {
  return (
    <div className="space-y-8">
      <header>
        <p className="dn-kicker text-text-secondary">CONTRIBUTOR</p>
        <h1 className="dn-display mt-1 text-3xl">贡献者入驻申请</h1>
      </header>

      <Notice tone="info" title="普通用户可直接投稿免费内容">
        入驻仅用于声明能力与权利材料，不影响投稿、免审与分成。
      </Notice>

      <ApplicationForm />
      <ApplicationHistory />
    </div>
  );
}

function ApplicationForm() {
  const queryClient = useQueryClient();
  const [capability, setCapability] = useState('');
  const [rightsRef, setRightsRef] = useState('');
  const [verificationRef, setVerificationRef] = useState('');
  const [contractPartyRef, setContractPartyRef] = useState('');

  const mutation = useMutation({
    mutationFn: () =>
      submitContributorApplication({
        capability_summary: capability.trim(),
        rights_materials_ref: rightsRef.trim(),
        verification_ref: verificationRef.trim() || null,
        contract_party_ref: contractPartyRef.trim() || null,
      }),
    onSuccess: () => {
      toast.success('入驻申请已提交');
      setCapability('');
      setRightsRef('');
      setVerificationRef('');
      setContractPartyRef('');
      queryClient.invalidateQueries({ queryKey: ['my-contributor-applications'] });
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const canSubmit =
    capability.trim().length > 0 &&
    rightsRef.trim().length > 0 &&
    !mutation.isPending;

  return (
    <section aria-labelledby="application-form" className="space-y-3">
      <h2 id="application-form" className="dn-display text-2xl">
        提交申请
      </h2>
      <form
        className="max-w-2xl space-y-4 border border-divider bg-surface p-5"
        onSubmit={(event) => {
          event.preventDefault();
          if (!canSubmit) return;
          mutation.mutate();
        }}
      >
        <fieldset disabled={mutation.isPending} className="space-y-4">
          <label className="block text-sm text-text-primary">
            能力说明（必填）
            <textarea
              value={capability}
              onChange={(event) => setCapability(event.target.value)}
              rows={4}
              maxLength={2000}
              required
              className={textareaClass}
              placeholder="说明可提供的专业内容范围与来源。"
            />
          </label>
          <Input
            label="权利材料引用（必填）"
            value={rightsRef}
            onChange={(event) => setRightsRef(event.target.value)}
            maxLength={512}
            required
            placeholder="存放权利材料的引用标识或链接，不粘贴原件。"
          />
          <Input
            label="身份核验引用（选填）"
            value={verificationRef}
            onChange={(event) => setVerificationRef(event.target.value)}
            maxLength={512}
          />
          <Input
            label="签约主体引用（选填）"
            value={contractPartyRef}
            onChange={(event) => setContractPartyRef(event.target.value)}
            maxLength={512}
          />
          <Button type="submit" isLoading={mutation.isPending} disabled={!canSubmit}>
            提交申请
          </Button>
        </fieldset>
      </form>
    </section>
  );
}

function ApplicationHistory() {
  const query = useInfiniteQuery({
    queryKey: ['my-contributor-applications'],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => listMyContributorApplications(pageParam),
    getNextPageParam: (page) => (page.has_more ? page.next_cursor : undefined),
  });
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <section aria-labelledby="application-history" className="space-y-3">
      <h2 id="application-history" className="dn-display text-2xl">
        申请记录
      </h2>
      {query.error ? (
        <Notice
          tone="warning"
          title="申请记录暂时无法加载"
          action={
            <Button variant="secondary" onClick={() => query.refetch()}>
              重试
            </Button>
          }
        >
          {describeError(query.error).message}
        </Notice>
      ) : query.isPending ? (
        <SkeletonList count={2} />
      ) : items.length === 0 ? (
        <EmptyState title="还没有入驻申请" description="提交后会在这里显示处理状态与时间线。" />
      ) : (
        <ul className="space-y-3">
          {items.map((item) => (
            <ApplicationItem key={item.id} application={item} />
          ))}
        </ul>
      )}
      {query.hasNextPage && (
        <Button
          variant="secondary"
          isLoading={query.isFetchingNextPage}
          onClick={() => query.fetchNextPage()}
        >
          更多记录
        </Button>
      )}
    </section>
  );
}

function ApplicationItem({ application }: { application: ContributorApplicationOut }) {
  const steps: { label: string; at: string; note?: string | null }[] = [
    { label: '提交申请', at: application.submitted_at },
  ];
  if (application.decided_at) {
    steps.push({
      label: `审核结果：${CONTRIBUTOR_ONBOARDING_STATUS_LABELS[application.status]}`,
      at: application.decided_at,
      note: application.decision_reason,
    });
  }
  if (application.closed_at) {
    steps.push({ label: '已关闭', at: application.closed_at });
  }

  return (
    <li className="space-y-3 border border-divider bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium text-text-primary">入驻申请</span>
        <Badge color={statusTone[application.status]}>
          {CONTRIBUTOR_ONBOARDING_STATUS_LABELS[application.status]}
        </Badge>
      </div>
      <dl className="space-y-1 text-sm">
        <div>
          <dt className="text-text-secondary">能力说明</dt>
          <dd className="whitespace-pre-wrap break-words text-text-primary">
            {application.capability_summary}
          </dd>
        </div>
        <div>
          <dt className="text-text-secondary">权利材料引用</dt>
          <dd className="break-words text-text-primary">{application.rights_materials_ref}</dd>
        </div>
      </dl>
      <ol className="space-y-2 border-l border-divider pl-4 text-sm">
        {steps.map((step) => (
          <li key={`${step.label}-${step.at}`}>
            <span className="text-text-primary">{step.label}</span>
            <time className="ml-3 text-text-secondary">{formatDateTime(step.at)}</time>
            {step.note && (
              <p className="mt-1 whitespace-pre-wrap break-words text-text-secondary">{step.note}</p>
            )}
          </li>
        ))}
      </ol>
      {(application.status === 'active' || application.status === 'suspended') && (
        <ContributorStatusForm application={application} selfExit />
      )}
    </li>
  );
}
