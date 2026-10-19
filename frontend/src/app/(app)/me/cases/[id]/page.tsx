'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';

import { Notice } from '@/components/common/Notice';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { SkeletonList } from '@/components/ui/Skeleton';
import { describeError } from '@/lib/api/client';
import {
  CASE_EVENT_TYPE_LABELS,
  CASE_STATUS_LABELS,
  CASE_SUBJECT_TYPE_LABELS,
  CASE_TYPE_LABELS,
  getMyCase,
  type CaseStatus,
} from '@/lib/api/cases';
import { formatDateTime } from '@/lib/utils';

const statusTone: Record<CaseStatus, 'gray' | 'yellow' | 'blue' | 'green' | 'orange'> = {
  open: 'yellow',
  triaged: 'blue',
  investigating: 'blue',
  decided: 'green',
  closed: 'gray',
};

export default function MyCaseDetailPage() {
  const { id } = useParams<{ id: string }>();
  const query = useQuery({
    queryKey: ['my-case', id],
    queryFn: () => getMyCase(id),
  });

  if (query.isPending) {
    return (
      <div className="space-y-6" role="status" aria-label="案件详情加载中">
        <SkeletonList count={2} />
      </div>
    );
  }

  if (query.error) {
    return (
      <div className="space-y-4">
        <Notice
          tone="warning"
          title="案件详情暂时无法加载"
          action={
            <Button variant="secondary" onClick={() => query.refetch()}>
              重试
            </Button>
          }
        >
          {describeError(query.error).message}
        </Notice>
        <BackLink />
      </div>
    );
  }

  const data = query.data;
  const events = data.events ?? [];

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <p className="dn-kicker text-text-secondary">CASE</p>
        <h1 className="dn-display break-words text-3xl">{data.title}</h1>
        <div className="flex flex-wrap items-center gap-3 text-sm text-text-secondary">
          <Badge color={statusTone[data.state]}>{CASE_STATUS_LABELS[data.state]}</Badge>
          <span>{CASE_TYPE_LABELS[data.case_type]}</span>
          <span>
            {CASE_SUBJECT_TYPE_LABELS[data.subject_type]} · 提交于{' '}
            {formatDateTime(data.created_at)}
          </span>
        </div>
      </header>

      {data.description && (
        <section aria-labelledby="case-reason" className="space-y-2">
          <h2 id="case-reason" className="dn-display text-xl">
            本人理由
          </h2>
          <p className="whitespace-pre-wrap break-words border border-divider bg-surface p-4 text-sm text-text-primary">
            {data.description}
          </p>
        </section>
      )}

      <section aria-labelledby="case-outcome" className="space-y-2">
        <h2 id="case-outcome" className="dn-display text-xl">
          对外结论
        </h2>
        {data.decision_reason ? (
          <div className="space-y-1 border border-divider bg-surface p-4 text-sm">
            <p className="whitespace-pre-wrap break-words text-text-primary">
              {data.decision_reason}
            </p>
            {data.decided_at && (
              <p className="text-xs text-text-secondary">
                决定时间 {formatDateTime(data.decided_at)}
              </p>
            )}
          </div>
        ) : (
          <p className="text-sm text-text-secondary">案件尚未给出对外结论。</p>
        )}
        {data.rule_version && (
          <p className="text-xs text-text-secondary">适用规则版本 {data.rule_version}</p>
        )}
      </section>

      <section aria-labelledby="case-timeline" className="space-y-2">
        <h2 id="case-timeline" className="dn-display text-xl">
          状态时间线
        </h2>
        {events.length === 0 ? (
          <p className="text-sm text-text-secondary">暂无状态流转记录。</p>
        ) : (
          <ol className="space-y-2 border-l border-divider pl-4 text-sm">
            {events.map((event) => (
              <li key={event.id} className="break-words">
                <span className="text-text-primary">
                  {CASE_EVENT_TYPE_LABELS[event.event_type]}
                </span>
                <time className="ml-3 text-text-secondary">{formatDateTime(event.created_at)}</time>
                {event.served_at && (
                  <p className="mt-1 text-xs text-text-secondary">
                    送达时间 {formatDateTime(event.served_at)}
                  </p>
                )}
                {event.deadline_at && (
                  <p className="text-xs text-text-secondary">
                    适用期限 {formatDateTime(event.deadline_at)}
                  </p>
                )}
              </li>
            ))}
          </ol>
        )}
      </section>

      <BackLink />
    </div>
  );
}

function BackLink() {
  return (
    <Link
      href="/me/cases"
      className="inline-flex min-h-11 items-center text-sm text-brand underline"
    >
      返回我的案件
    </Link>
  );
}
