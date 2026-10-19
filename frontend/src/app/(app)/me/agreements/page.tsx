'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';

import { Notice } from '@/components/common/Notice';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { SkeletonList } from '@/components/ui/Skeleton';
import { ApiError, describeError } from '@/lib/api/client';
import {
  AGREEMENT_STATE_LABELS,
  listMyAgreements,
  type AgreementDetailOut,
  type AgreementState,
  type AgreementVersionOut,
} from '@/lib/api/licensing';
import { formatDateTime } from '@/lib/utils';
import { useAuthStore } from '@/stores/authStore';
import { fetchFeatures } from '@/lib/api/features';
import { acceptAgreement } from '@/lib/api/licensing';
import { PriceProposals } from '@/components/workspace/PriceProposals';

const stateTone: Record<AgreementState, 'gray' | 'yellow' | 'green' | 'orange' | 'red'> = {
  draft: 'gray',
  awaiting_acceptance: 'yellow',
  active: 'green',
  suspended: 'orange',
  expired: 'gray',
  terminated: 'red',
};

function NotEnabled() {
  return (
    <Notice tone="info" title="协议与定价尚未启用">
      商业化关闭时不会产生协议与报价；当前无需在此处理任何条款。
    </Notice>
  );
}

export default function MyAgreementsPage() {
  const user = useAuthStore((s) => s.user);
  const query = useQuery({
    queryKey: ['my-agreements', user?.id],
    queryFn: listMyAgreements,
    enabled: !!user,
  });

  return (
    <div className="space-y-6">
      <header>
        <p className="dn-kicker text-text-secondary">AGREEMENTS</p>
        <h1 className="dn-display mt-1 text-3xl">我的协议</h1>
      </header>

      {query.isPending ? (
        <SkeletonList count={2} />
      ) : query.error ? (
        errorIsNotEnabled(query.error) ? (
          <NotEnabled />
        ) : (
          <Notice
            tone="warning"
            title="协议暂时无法加载"
            action={
              <Button variant="secondary" onClick={() => query.refetch()}>
                重试
              </Button>
            }
          >
            {describeError(query.error).message}
          </Notice>
        )
      ) : (query.data?.length ?? 0) === 0 ? (
        <Notice title="暂无协议">本人参与的协议与历史版本会显示在这里。</Notice>
      ) : (
        <ul className="space-y-4">
          {query.data?.map((agreement) => (
            <AgreementItem key={agreement.id} agreement={agreement} />
          ))}
        </ul>
      )}
      <PriceProposals />
    </div>
  );
}

function errorIsNotEnabled(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'FEATURE_NOT_ENABLED';
}

function AgreementItem({ agreement }: { agreement: AgreementDetailOut }) {
  return (
    <li className="space-y-3 border border-divider bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="break-words font-medium text-text-primary">{agreement.title}</p>
          <p className="mt-1 text-xs text-text-secondary">
            创建于 {formatDateTime(agreement.created_at)} · 更新于{' '}
            {formatDateTime(agreement.updated_at)}
          </p>
        </div>
        <Badge color={stateTone[agreement.state]}>{AGREEMENT_STATE_LABELS[agreement.state]}</Badge>
      </div>

      <section aria-label="历史版本" className="space-y-2">
        <h2 className="text-sm font-semibold text-text-primary">历史版本</h2>
        {agreement.versions.length === 0 ? (
          <p className="text-sm text-text-secondary">暂无版本记录。</p>
        ) : (
          <ol className="space-y-2">
            {agreement.versions.map((version) => (
              <VersionItem key={version.id} version={version} />
            ))}
          </ol>
        )}
      </section>
    </li>
  );
}

function VersionItem({ version }: { version: AgreementVersionOut }) {
  const client = useQueryClient();
  const [checked, setChecked] = useState(false);
  const features = useQuery({ queryKey: ['features'], queryFn: fetchFeatures });
  const mutation = useMutation({
    mutationFn: () => acceptAgreement(version),
    onSuccess: () => {
      toast.success('已记录本版本确认');
      client.invalidateQueries({ queryKey: ['my-agreements'] });
    },
    onError: error => toast.error(describeError(error).message),
  });
  const scopeEntries = Object.entries(version.rights_scope);
  return (
    <li className="space-y-1 border border-divider bg-canvas p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium text-text-primary">版本 v{version.version_no}</span>
        <span className="text-text-secondary">{AGREEMENT_STATE_LABELS[version.state]}</span>
      </div>
      <p className="break-all text-xs text-text-secondary">条款哈希 {version.terms_hash}</p>
      {version.territory && (
        <p className="text-xs text-text-secondary">适用地域 {version.territory}</p>
      )}
      {(version.starts_at || version.ends_at) && (
        <p className="text-xs text-text-secondary">
          {version.starts_at ? `生效 ${formatDateTime(version.starts_at)}` : ''}
          {version.starts_at && version.ends_at ? ' · ' : ''}
          {version.ends_at ? `结束 ${formatDateTime(version.ends_at)}` : ''}
        </p>
      )}
      {scopeEntries.length > 0 && (
        <dl className="space-y-1">
          {scopeEntries.map(([key, value]) => (
            <div key={key} className="flex flex-wrap gap-x-2">
              <dt className="text-text-secondary">{key}</dt>
              <dd className="break-words text-text-primary">
                {typeof value === 'string' ? value : JSON.stringify(value)}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {features.data?.commerce_enabled && version.state === 'awaiting_acceptance' && (
        <div className="space-y-2 border-t border-divider pt-2">
          <label className="flex min-h-11 items-center gap-2 text-sm">
            <input type="checkbox" checked={checked} onChange={event => setChecked(event.target.checked)} />
            已核对收到的完整协议、附件及本版本条款哈希
          </label>
          <Button disabled={!checked} isLoading={mutation.isPending} onClick={() => mutation.mutate()}>确认此协议版本</Button>
        </div>
      )}
    </li>
  );
}
