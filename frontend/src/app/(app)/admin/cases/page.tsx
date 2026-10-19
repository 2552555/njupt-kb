'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';

import { Notice } from '@/components/common/Notice';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { SkeletonList } from '@/components/ui/Skeleton';
import { describeError } from '@/lib/api/client';
import {
  appendCaseEvent,
  CASE_EVENT_TYPE_LABELS,
  CASE_MEASURE_KIND_LABELS,
  CASE_MEASURE_STATUS_LABELS,
  CASE_STATUS_LABELS,
  CASE_SUBJECT_TYPE_LABELS,
  CASE_TYPE_LABELS,
  CASE_TYPE_ORDER,
  listAdminCases,
  releaseMeasure,
  takeMeasure,
  type CaseAdminOut,
  type CaseEventType,
  type CaseMeasureKind,
  type CaseStatus,
  type CaseSubjectType,
  type CaseType,
  type MeasureOut,
} from '@/lib/api/cases';
import { formatDateTime, isUuid } from '@/lib/utils';
import { hasPermission, useAuthStore } from '@/stores/authStore';

const statusTone: Record<CaseStatus, 'gray' | 'yellow' | 'blue' | 'green' | 'orange'> = {
  open: 'yellow',
  triaged: 'blue',
  investigating: 'blue',
  decided: 'green',
  closed: 'gray',
};

const selectClass =
  'mt-1 min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus';

const AMOUNT_MEASURES: CaseMeasureKind[] = ['block_sales', 'freeze_revenue'];

export default function AdminCasesPage() {
  const user = useAuthStore((s) => s.user);
  return hasPermission(user, 'case.handle') ? <CasesPanel /> : <Notice title="需要案件处理权限" />;
}

function CasesPanel() {
  const [caseType, setCaseType] = useState<CaseType | ''>('');
  const [state, setState] = useState<CaseStatus | ''>('');
  const [active, setActive] = useState<CaseAdminOut | null>(null);

  const listQuery = useQuery({
    queryKey: ['admin-cases', caseType, state],
    queryFn: () =>
      listAdminCases({
        case_type: caseType ? [caseType] : undefined,
        state: state ? [state] : undefined,
      }),
  });
  const items = listQuery.data?.items ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-4">
        <label className="flex items-center gap-3 text-sm">
          案件类型
          <select
            value={caseType}
            onChange={(event) => setCaseType(event.target.value as CaseType | '')}
            className="min-h-11 border border-control-border bg-surface px-3"
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
            className="min-h-11 border border-control-border bg-surface px-3"
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

      {listQuery.isLoading ? (
        <SkeletonList count={3} />
      ) : listQuery.error ? (
        <Notice
          tone="warning"
          title="案件队列暂时无法加载"
          action={
            <Button variant="secondary" onClick={() => listQuery.refetch()}>
              重试
            </Button>
          }
        >
          {describeError(listQuery.error).message}
        </Notice>
      ) : items.length === 0 ? (
        <EmptyState title="当前没有符合条件的案件" description="侵权通知与申诉会进入这里。" />
      ) : (
        <ul className="space-y-3">
          {items.map((item) => (
            <li key={item.id} className="border border-divider bg-surface p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="break-words font-medium text-text-primary">{item.title}</p>
                  <p className="mt-1 text-xs text-text-secondary">
                    {CASE_TYPE_LABELS[item.case_type]} · 申请人 {item.applicant_id.slice(0, 8)}… ·
                    提交于 {formatDateTime(item.created_at)}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <Badge color={statusTone[item.state]}>{CASE_STATUS_LABELS[item.state]}</Badge>
                  <Button variant="primary" onClick={() => setActive(item)}>
                    处理
                  </Button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}

      {active && (
        <CaseModal
          caseItem={active}
          onClose={() => setActive(null)}
          onChanged={() => {
            listQuery.refetch();
          }}
        />
      )}
    </div>
  );
}

function CaseModal({
  caseItem,
  onClose,
  onChanged,
}: {
  caseItem: CaseAdminOut;
  onClose: () => void;
  onChanged: () => void;
}) {
  const queryClient = useQueryClient();
  const [eventType, setEventType] = useState<CaseEventType>('triaged');
  const [eventReason, setEventReason] = useState('');
  const [eventNote, setEventNote] = useState('');
  const [servedAt, setServedAt] = useState('');
  const [deadlineAt, setDeadlineAt] = useState('');

  const [measureKind, setMeasureKind] = useState<CaseMeasureKind>('unpublish');
  const [measureTargetType, setMeasureTargetType] = useState<CaseSubjectType>(caseItem.subject_type);
  const [measureTargetId, setMeasureTargetId] = useState(caseItem.subject_id);
  const [measureReason, setMeasureReason] = useState('');
  const [amountFen, setAmountFen] = useState('');
  const [lastMeasure, setLastMeasure] = useState<MeasureOut | null>(null);

  const [releaseId, setReleaseId] = useState('');

  const eventMutation = useMutation({
    mutationFn: () =>
      appendCaseEvent(caseItem.id, {
        event_type: eventType,
        reason: eventReason.trim(),
        note: eventNote.trim() || null,
        served_at: toIso(servedAt),
        deadline_at: toIso(deadlineAt),
      }),
    onSuccess: () => {
      toast.success('案件事件已记录');
      setEventReason('');
      setEventNote('');
      setServedAt('');
      setDeadlineAt('');
      onChanged();
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const measureMutation = useMutation({
    mutationFn: () =>
      takeMeasure(caseItem.id, {
        measure: measureKind,
        target_type: measureTargetType,
        target_id: measureTargetId.trim(),
        reason: measureReason.trim(),
        amount_fen:
          AMOUNT_MEASURES.includes(measureKind) && amountFen.trim()
            ? Number(amountFen)
            : undefined,
      }),
    onSuccess: (measure) => {
      toast.success('措施已记录');
      setLastMeasure(measure);
      setMeasureReason('');
      setAmountFen('');
      queryClient.invalidateQueries({ queryKey: ['admin-cases'] });
      onChanged();
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const releaseMutation = useMutation({
    mutationFn: (measureId: string) => releaseMeasure(caseItem.id, measureId),
    onSuccess: () => {
      toast.success('措施已解除');
      setReleaseId('');
      setLastMeasure(null);
      onChanged();
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const amountInvalid =
    AMOUNT_MEASURES.includes(measureKind) &&
    amountFen.trim().length > 0 &&
    !/^\d+$/.test(amountFen.trim());

  return (
    <Modal isOpen onClose={onClose} title={`案件 · ${caseItem.id.slice(0, 8)}…`}>
      <div className="space-y-6">
        <section className="space-y-1 text-sm">
          <p className="break-words text-text-primary">{caseItem.title}</p>
          <p className="text-text-secondary">
            {CASE_TYPE_LABELS[caseItem.case_type]} ·{' '}
            {CASE_SUBJECT_TYPE_LABELS[caseItem.subject_type]} {caseItem.subject_id} ·{' '}
            {CASE_STATUS_LABELS[caseItem.state]}
          </p>
          {caseItem.rule_version && (
            <p className="text-text-secondary">适用规则版本 {caseItem.rule_version}</p>
          )}
          {caseItem.description && (
            <p className="whitespace-pre-wrap break-words border-l-2 border-divider pl-3 text-text-secondary">
              {caseItem.description}
            </p>
          )}
          {caseItem.decision_reason && (
            <p className="whitespace-pre-wrap break-words text-text-secondary">
              已有结论：{caseItem.decision_reason}
            </p>
          )}
          <p className="text-xs text-text-secondary">
            申请人 {caseItem.applicant_id}
            {caseItem.handler_id ? ` · 处理人 ${caseItem.handler_id}` : ''}
          </p>
        </section>

        <section aria-labelledby="case-event-form" className="space-y-3 border-t border-divider pt-4">
          <h3 id="case-event-form" className="text-sm font-semibold text-text-primary">
            推进案件事件
          </h3>
          <form
            className="space-y-3"
            onSubmit={(event) => {
              event.preventDefault();
              if (!eventReason.trim()) {
                toast.error('请填写事件理由');
                return;
              }
              eventMutation.mutate();
            }}
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block text-sm text-text-primary">
                事件类型
                <select
                  value={eventType}
                  onChange={(event) => setEventType(event.target.value as CaseEventType)}
                  className={selectClass}
                >
                  {Object.entries(CASE_EVENT_TYPE_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block text-sm text-text-primary">
                送达时间（选填）
                <input
                  type="datetime-local"
                  value={servedAt}
                  onChange={(event) => setServedAt(event.target.value)}
                  className={selectClass}
                />
              </label>
              <label className="block text-sm text-text-primary">
                适用期限（选填）
                <input
                  type="datetime-local"
                  value={deadlineAt}
                  onChange={(event) => setDeadlineAt(event.target.value)}
                  className={selectClass}
                />
              </label>
            </div>
            <label className="block text-sm text-text-primary">
              事件理由（必填）
              <textarea
                value={eventReason}
                onChange={(event) => setEventReason(event.target.value)}
                rows={3}
                maxLength={5000}
                required
                className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3 py-2 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
              />
            </label>
            <label className="block text-sm text-text-primary">
              内部备注（选填，不对申请人展示）
              <textarea
                value={eventNote}
                onChange={(event) => setEventNote(event.target.value)}
                rows={2}
                maxLength={5000}
                className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3 py-2 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
              />
            </label>
            <div className="flex justify-end">
              <Button type="submit" isLoading={eventMutation.isPending} disabled={!eventReason.trim()}>
                记录事件
              </Button>
            </div>
          </form>
        </section>

        <section aria-labelledby="case-measure-form" className="space-y-3 border-t border-divider pt-4">
          <h3 id="case-measure-form" className="text-sm font-semibold text-text-primary">
            采取必要措施
          </h3>
          <form
            className="space-y-3"
            onSubmit={(event) => {
              event.preventDefault();
              if (!measureReason.trim()) {
                toast.error('请填写措施理由');
                return;
              }
              if (!isUuid(measureTargetId.trim())) {
                toast.error('对象标识需为有效的 UUID');
                return;
              }
              if (amountInvalid) {
                toast.error('金额需为非负整数分');
                return;
              }
              measureMutation.mutate();
            }}
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block text-sm text-text-primary">
                措施类型
                <select
                  value={measureKind}
                  onChange={(event) => setMeasureKind(event.target.value as CaseMeasureKind)}
                  className={selectClass}
                >
                  {Object.entries(CASE_MEASURE_KIND_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block text-sm text-text-primary">
                对象类型
                <select
                  value={measureTargetType}
                  onChange={(event) => setMeasureTargetType(event.target.value as CaseSubjectType)}
                  className={selectClass}
                >
                  {Object.entries(CASE_SUBJECT_TYPE_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <Input
              label="对象标识（必填）"
              value={measureTargetId}
              onChange={(event) => setMeasureTargetId(event.target.value)}
            />
            {AMOUNT_MEASURES.includes(measureKind) && (
              <Input
                label="涉及金额（分，选填）"
                inputMode="numeric"
                value={amountFen}
                onChange={(event) => setAmountFen(event.target.value)}
                error={amountInvalid ? '需为非负整数分' : undefined}
              />
            )}
            <label className="block text-sm text-text-primary">
              措施理由（必填）
              <textarea
                value={measureReason}
                onChange={(event) => setMeasureReason(event.target.value)}
                rows={3}
                maxLength={5000}
                required
                className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3 py-2 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
              />
            </label>
            <div className="flex justify-end">
              <Button
                type="submit"
                isLoading={measureMutation.isPending}
                disabled={!measureReason.trim() || amountInvalid}
              >
                采取措施
              </Button>
            </div>
          </form>

          {lastMeasure && (
            <div className="space-y-2 border border-divider bg-canvas p-3 text-sm">
              <p className="text-text-primary">
                已记录措施：{CASE_MEASURE_KIND_LABELS[lastMeasure.measure]} ·{' '}
                {CASE_MEASURE_STATUS_LABELS[lastMeasure.status]}
              </p>
              <p className="break-all text-xs text-text-secondary">措施编号 {lastMeasure.id}</p>
              {!lastMeasure.effect_applied && (
                <p className="text-xs text-text-secondary">
                  商业化关闭时该措施只记录，不实际作用资金。
                </p>
              )}
              <Button
                variant="secondary"
                isLoading={releaseMutation.isPending}
                onClick={() => releaseMutation.mutate(lastMeasure.id)}
              >
                解除该措施
              </Button>
            </div>
          )}
        </section>

        <section aria-labelledby="case-measure-release" className="space-y-3 border-t border-divider pt-4">
          <h3 id="case-measure-release" className="text-sm font-semibold text-text-primary">
            解除已有措施
          </h3>
          <p className="text-sm text-text-secondary">
            填入措施编号即可解除；编号来自采取措施时的回执。
          </p>
          <Input
            label="措施编号（UUID）"
            value={releaseId}
            onChange={(event) => setReleaseId(event.target.value)}
          />
          <div className="flex justify-end">
            <Button
              variant="secondary"
              isLoading={releaseMutation.isPending}
              disabled={!isUuid(releaseId.trim())}
              onClick={() => releaseMutation.mutate(releaseId.trim())}
            >
              解除措施
            </Button>
          </div>
        </section>

        <div className="flex justify-end border-t border-divider pt-4">
          <Button variant="ghost" onClick={onClose}>
            关闭
          </Button>
        </div>
      </div>
    </Modal>
  );
}

/** datetime-local 值转 ISO；空值返回 null。 */
function toIso(value: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}
