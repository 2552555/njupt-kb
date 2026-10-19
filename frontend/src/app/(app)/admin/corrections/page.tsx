'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { PencilLine } from 'lucide-react';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { Modal } from '@/components/ui/Modal';
import { SkeletonList } from '@/components/ui/Skeleton';
import { describeError } from '@/lib/api/client';
import {
  listCorrections,
  updateCorrectionStatus,
  type CorrectionItem,
  applyClassificationFix,
} from '@/lib/api/interaction';
import { getResource } from '@/lib/api/content';
import { getCourse } from '@/lib/api/catalog';
import { RESOURCE_TYPE_LABELS } from '@/lib/api/types';
import { isAdmin, useAuthStore } from '@/stores/authStore';
import { isUuid, formatDateTime } from '@/lib/utils';

type CorrectionStatus = 'open' | 'accepted' | 'rejected' | 'fixed';

const statusLabels: Record<CorrectionStatus, string> = {
  open: '待处理',
  accepted: '已接受',
  rejected: '已拒绝',
  fixed: '已修复',
};

const statusTones: Record<CorrectionStatus, string> = {
  open: 'bg-amber text-on-color',
  accepted: 'bg-emerald text-on-color',
  rejected: 'bg-crimson text-on-color',
  fixed: 'bg-cyan text-on-color',
};

const filters: { value: 'open' | 'all'; label: string }[] = [
  { value: 'open', label: '待处理' },
  { value: 'all', label: '全部' },
];

export default function AdminCorrectionsPage() {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<'open' | 'all'>('open');
  const [active, setActive] = useState<CorrectionItem | null>(null);

  const listQuery = useQuery({
    queryKey: ['admin-corrections', filter],
    queryFn: () =>
      listCorrections({ status: filter === 'all' ? undefined : 'open', limit: 20 }),
  });

  const count = listQuery.data?.items.length ?? 0;

  return (
    <div className="space-y-6">
      <p className="max-w-2xl text-sm text-text-secondary">
        接受或修复会记为该提交者的正向贡献，不会惩罚被纠错的贡献者（§20.2）。
      </p>

      <div className="flex flex-wrap items-center gap-2">
        {filters.map((item) => (
          <button
            key={item.value}
            type="button"
            aria-pressed={filter === item.value}
            onClick={() => setFilter(item.value)}
            className={`inline-flex min-h-11 items-center px-3 text-sm font-medium ${
              filter === item.value
                ? 'bg-brand text-on-color'
                : 'border border-control-border text-text-primary hover:bg-canvas'
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>

      {listQuery.isLoading ? (
        <SkeletonList count={3} />
      ) : listQuery.error ? (
        <Notice
          tone="warning"
          title="纠错工单暂时无法加载"
          action={<Button variant="secondary" onClick={() => listQuery.refetch()}>重试</Button>}
        >
          {describeError(listQuery.error).message}
        </Notice>
      ) : count > 0 ? (
        <ul className="space-y-3">
          {listQuery.data?.items.map((item) => (
            <li key={item.id} className="border border-divider bg-surface p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-medium text-text-primary">
                    <span
                      className={`mr-2 px-2 py-0.5 text-xs font-semibold ${
                        statusTones[item.status as CorrectionStatus] ?? 'bg-steel text-on-color'
                      }`}
                    >
                      {statusLabels[item.status as CorrectionStatus] ?? item.status}
                    </span>
                    {item.description}
                  </p>
                  <p className="mt-1 text-xs text-text-secondary">
                    资源 {item.resource_id.slice(0, 8)}… · 版本 {item.version_id.slice(0, 8)}… ·
                    提交于 {formatDateTime(item.created_at)}
                  </p>
                  {item.resolution_note && (
                    <p className="mt-1 text-xs text-text-secondary">
                      处理说明：{item.resolution_note}
                    </p>
                  )}
                </div>
                <Button
                  variant="primary"
                  disabled={item.status !== 'open' && item.status !== 'accepted'}
                  onClick={() => setActive(item)}
                >
                  {item.status === 'open' || item.status === 'accepted' ? '处理' : '已处理'}
                </Button>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState
          icon={PencilLine}
          title="当前没有待处理纠错"
          description="用户提交的内容纠错会进入这里。"
        />
      )}

      {active && (
        <CorrectionModal
          correction={active}
          onClose={() => setActive(null)}
          onChanged={() => {
            queryClient.invalidateQueries({ queryKey: ['admin-corrections'] });
            queryClient.invalidateQueries({ queryKey: ['admin-moderation-summary'] });
          }}
        />
      )}
    </div>
  );
}

function CorrectionModal({
  correction,
  onClose,
  onChanged,
}: {
  correction: CorrectionItem;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [status, setStatus] = useState<'accepted' | 'rejected' | 'fixed'>('accepted');
  const [note, setNote] = useState('');
  const [fixedVersionId, setFixedVersionId] = useState('');
  const user = useAuthStore(s => s.user);
  const classification = !!(correction.suggested_course_id || correction.suggested_resource_type);
  const resource = useQuery({ queryKey: ['correction-resource', correction.resource_id], queryFn: () => getResource(correction.resource_id), enabled: classification });
  const course = useQuery({ queryKey: ['correction-course', correction.suggested_course_id], queryFn: () => getCourse(correction.suggested_course_id!), enabled: !!correction.suggested_course_id });
  const [apply, setApply] = useState(false);

  const mutation = useMutation({
    mutationFn: () => {
      if (apply) {
        if (!resource.data || !note.trim()) throw new Error('请等待资料加载并填写处理说明');
        return applyClassificationFix(correction.id, resource.data.revision, note.trim());
      }
      return updateCorrectionStatus(correction.id, {
        status,
        resolution_note: note.trim() || undefined,
        fixed_version_id:
          status === 'fixed' && fixedVersionId.trim() ? fixedVersionId.trim() : undefined,
      });
    },
    onSuccess: () => {
      toast.success('纠错工单已处理');
      onChanged();
      onClose();
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const invalidVersionId =
    status === 'fixed' && fixedVersionId.trim() !== '' && !isUuid(fixedVersionId.trim());

  return (
    <Modal isOpen onClose={onClose} title={`纠错工单 · ${correction.id.slice(0, 8)}…`}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          mutation.mutate();
        }}
      >
        <p className="text-sm text-text-secondary">{correction.description}</p>
        {classification && <div className="space-y-2 border border-divider p-3 text-sm">
          <p>建议课程：{correction.suggested_course_id ? course.data?.name || correction.suggested_course_id : '保持不变'}</p>
          <p>建议类型：{correction.suggested_resource_type ? RESOURCE_TYPE_LABELS[correction.suggested_resource_type] : '保持不变'}</p>
          {isAdmin(user) && <label className="flex items-start gap-2"><input type="checkbox" checked={apply} disabled={!resource.data || !!resource.error} onChange={e => setApply(e.target.checked)} />采纳建议并修复分类（同步关闭工单、通知提交者）</label>}
          {resource.error && <p role="alert">{describeError(resource.error).message}</p>}
        </div>}
        <p className="text-xs text-text-secondary">
          资源{' '}
          <Link
            href={`/resources/${correction.resource_id}`}
            className="underline underline-offset-2 hover:text-text-primary"
          >
            {correction.resource_id}
          </Link>
          {' · 版本 '}
          {correction.version_id.slice(0, 8)}…
        </p>

        <label className="block text-sm font-medium text-text-primary">
          处理结论
          <select
            value={status}
            disabled={apply}
            onChange={(event) => setStatus(event.target.value as typeof status)}
            className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
          >
            <option value="accepted">接受纠错</option>
            <option value="rejected">拒绝纠错</option>
            <option value="fixed">已修复</option>
          </select>
        </label>

        {status === 'fixed' && (
          <label className="block text-sm font-medium text-text-primary">
            修复版本 ID（可选）
            <input
              value={fixedVersionId}
              onChange={(event) => setFixedVersionId(event.target.value)}
              placeholder="修复该问题的新版本"
              className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
            />
            {invalidVersionId && (
              <span className="mt-1 block text-xs text-crimson">需要有效的版本 UUID</span>
            )}
          </label>
        )}

        <label className="block text-sm font-medium text-text-primary">
          处理说明（建议填写）
          <textarea
            value={note}
            onChange={(event) => setNote(event.target.value)}
            rows={3}
            maxLength={2000}
            className="mt-1 w-full border border-control-border bg-surface px-3 py-2 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
            placeholder="说明判断依据，提交者会收到结果通知。"
          />
        </label>

        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button type="submit" isLoading={mutation.isPending} disabled={invalidVersionId}>
            提交处理
          </Button>
        </div>
      </form>
    </Modal>
  );
}
