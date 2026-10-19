'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { SkeletonList } from '@/components/ui/Skeleton';
import { describeError } from '@/lib/api/client';
import {
  ADMIN_PERMISSION_OPTIONS,
  ADMIN_PERMISSION_SCOPE_LABELS,
  ASSIGNMENT_CAPABILITY_LABELS,
  grantPermissionAssignment,
  grantResourceAssignment,
  listPermissionAssignments,
  listResourceAssignments,
  revokePermissionAssignment,
  revokeResourceAssignment,
  type AssignmentCapability,
  type PermissionAssignmentOut,
  type ResourceAssignmentOut,
} from '@/lib/api/assignment';
import { formatDateTime, isUuid } from '@/lib/utils';
import { hasPermission, useAuthStore } from '@/stores/authStore';

const textareaClass =
  'mt-1 min-h-11 w-full border border-control-border bg-surface px-3 py-2 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus';

const selectClass =
  'mt-1 min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus';

export default function AdminAssignmentsPage() {
  const user = useAuthStore((s) => s.user);
  if (!hasPermission(user, 'assignment.manage')) return <Notice title="需要管理员权限" />;
  return (
    <div className="space-y-10">
      <PermissionPanel />
      <ResourcePanel />
    </div>
  );
}

// --------------------------------------------------------------------------- //
// 系统管理员权限分配
// --------------------------------------------------------------------------- //

function PermissionPanel() {
  const queryClient = useQueryClient();
  const [userId, setUserId] = useState('');
  const [permission, setPermission] = useState(ADMIN_PERMISSION_OPTIONS[0]?.value ?? '');
  const [validUntil, setValidUntil] = useState('');
  const [reason, setReason] = useState('');
  const [revoking, setRevoking] = useState<PermissionAssignmentOut | null>(null);

  const listQuery = useQuery({
    queryKey: ['admin-permission-assignments'],
    queryFn: () => listPermissionAssignments(),
  });
  const items = listQuery.data?.items ?? [];

  const mutation = useMutation({
    mutationFn: () =>
      grantPermissionAssignment({
        user_id: userId.trim(),
        permission,
        valid_until: toIso(validUntil),
        reason: reason.trim(),
      }),
    onSuccess: () => {
      toast.success('已授予权限');
      setUserId('');
      setValidUntil('');
      setReason('');
      queryClient.invalidateQueries({ queryKey: ['admin-permission-assignments'] });
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const invalid =
    !isUuid(userId.trim()) ||
    reason.trim().length === 0;

  return (
    <section aria-labelledby="permission-assignments" className="space-y-4">
      <h2 id="permission-assignments" className="dn-display text-2xl">
        系统管理员权限分配
      </h2>
      <p className="text-sm text-text-secondary">
        后台职责权限覆盖全站；资料维护范围在下方按课程或资料单独分配。到期即失效，可随时撤销。
      </p>

      <form
        className="max-w-2xl space-y-4 border border-divider bg-surface p-5"
        onSubmit={(event) => {
          event.preventDefault();
          if (invalid) {
            toast.error('请检查用户标识与理由');
            return;
          }
          mutation.mutate();
        }}
      >
        <fieldset disabled={mutation.isPending} className="space-y-4">
          <Input
            label="用户标识（UUID，必填）"
            value={userId}
            onChange={(event) => setUserId(event.target.value)}
            required
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block text-sm text-text-primary">
              权限（必填）
              <select
                value={permission}
                onChange={(event) => setPermission(event.target.value)}
                className={selectClass}
              >
                {ADMIN_PERMISSION_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label className="block text-sm text-text-primary">
            到期时间（选填）
            <input
              type="datetime-local"
              value={validUntil}
              onChange={(event) => setValidUntil(event.target.value)}
              className={selectClass}
            />
          </label>
          <label className="block text-sm text-text-primary">
            授予理由（必填）
            <textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              rows={2}
              maxLength={255}
              required
              className={textareaClass}
            />
          </label>
          <Button type="submit" isLoading={mutation.isPending} disabled={invalid}>
            授予权限
          </Button>
        </fieldset>
      </form>

      {listQuery.isLoading ? (
        <SkeletonList count={2} />
      ) : listQuery.error ? (
        <Notice
          tone="warning"
          title="权限分配暂时无法加载"
          action={
            <Button variant="secondary" onClick={() => listQuery.refetch()}>
              重试
            </Button>
          }
        >
          {describeError(listQuery.error).message}
        </Notice>
      ) : items.length === 0 ? (
        <EmptyState title="还没有权限分配记录" />
      ) : (
        <ul className="space-y-3">
          {items.map((item) => (
            <li key={item.id} className="border border-divider bg-surface p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 space-y-1 text-sm">
                  <p className="break-words font-medium text-text-primary">{item.permission}</p>
                  <p className="text-xs text-text-secondary">
                    用户 {item.user_id.slice(0, 8)}… · 范围{' '}
                    {ADMIN_PERMISSION_SCOPE_LABELS[item.scope]}
                    {item.valid_until ? ` · 到期 ${formatDateTime(item.valid_until)}` : ' · 长期有效'}
                    {(item.scope !== 'global' || item.scope_catalog_id || item.scope_resource_id) && ' · 旧范围记录不再生效，需重新核对授权'}
                  </p>
                  {item.reason && (
                    <p className="break-words text-text-secondary">理由：{item.reason}</p>
                  )}
                  {item.revoked_at && (
                    <p className="text-text-secondary">
                      已撤销于 {formatDateTime(item.revoked_at)}
                      {item.revoked_reason ? ` · ${item.revoked_reason}` : ''}
                    </p>
                  )}
                </div>
                {!item.revoked_at && (
                  <Button variant="secondary" onClick={() => setRevoking(item)}>
                    撤销
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {revoking && (
        <RevokeModal
          title={`撤销权限 · ${revoking.permission}`}
          onClose={() => setRevoking(null)}
          onConfirm={(revokeReason) =>
            revokePermissionAssignment(revoking.id, revokeReason).then(() => {
              queryClient.invalidateQueries({ queryKey: ['admin-permission-assignments'] });
            })
          }
        />
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- //
// 资料管理员资源分配
// --------------------------------------------------------------------------- //

function ResourcePanel() {
  const queryClient = useQueryClient();
  const [userId, setUserId] = useState('');
  const [target, setTarget] = useState<'resource' | 'course'>('resource');
  const [targetId, setTargetId] = useState('');
  const [capability, setCapability] = useState<AssignmentCapability>('maintain');
  const [effectiveAt, setEffectiveAt] = useState('');
  const [reason, setReason] = useState('');
  const [revoking, setRevoking] = useState<ResourceAssignmentOut | null>(null);

  const listQuery = useQuery({
    queryKey: ['admin-resource-assignments'],
    queryFn: () => listResourceAssignments(),
  });
  const items = listQuery.data?.items ?? [];

  const mutation = useMutation({
    mutationFn: () =>
      grantResourceAssignment({
        user_id: userId.trim(),
        resource_id: target === 'resource' ? targetId.trim() : null,
        course_id: target === 'course' ? targetId.trim() : null,
        capability,
        effective_at: toIso(effectiveAt),
        reason: reason.trim(),
      }),
    onSuccess: () => {
      toast.success('已授予资源分配');
      setUserId('');
      setTargetId('');
      setEffectiveAt('');
      setReason('');
      queryClient.invalidateQueries({ queryKey: ['admin-resource-assignments'] });
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const invalid =
    !isUuid(userId.trim()) || !isUuid(targetId.trim()) || reason.trim().length === 0;

  return (
    <section aria-labelledby="resource-assignments" className="space-y-4">
      <h2 id="resource-assignments" className="dn-display text-2xl">
        资料管理员资源分配
      </h2>
      <p className="text-sm text-text-secondary">
        资料与课程二选一；分配决定「可对哪一个对象」执行管理动作。
      </p>

      <form
        className="max-w-2xl space-y-4 border border-divider bg-surface p-5"
        onSubmit={(event) => {
          event.preventDefault();
          if (invalid) {
            toast.error('请检查用户标识、对象标识与理由');
            return;
          }
          mutation.mutate();
        }}
      >
        <fieldset disabled={mutation.isPending} className="space-y-4">
          <Input
            label="用户标识（UUID，必填）"
            value={userId}
            onChange={(event) => setUserId(event.target.value)}
            required
          />
          <fieldset className="space-y-2">
            <legend className="text-sm text-text-primary">分配对象</legend>
            <div className="flex flex-wrap gap-4">
              {(
                [
                  ['resource', '资料'],
                  ['course', '课程'],
                ] as const
              ).map(([value, label]) => (
                <label key={value} className="flex min-h-11 items-center gap-2 text-sm text-text-primary">
                  <input
                    type="radio"
                    name="resource-assignment-target"
                    value={value}
                    checked={target === value}
                    onChange={() => {
                      setTarget(value);
                      setTargetId('');
                    }}
                  />
                  {label}
                </label>
              ))}
            </div>
          </fieldset>
          <Input
            label={`${target === 'resource' ? '资料' : '课程'}标识（UUID，必填）`}
            value={targetId}
            onChange={(event) => setTargetId(event.target.value)}
            required
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block text-sm text-text-primary">
              能力
              <select
                value={capability}
                onChange={(event) => setCapability(event.target.value as AssignmentCapability)}
                className={selectClass}
              >
                {Object.entries(ASSIGNMENT_CAPABILITY_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-sm text-text-primary">
              生效时间（选填）
              <input
                type="datetime-local"
                value={effectiveAt}
                onChange={(event) => setEffectiveAt(event.target.value)}
                className={selectClass}
              />
            </label>
          </div>
          <label className="block text-sm text-text-primary">
            分配理由（必填）
            <textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              rows={2}
              maxLength={255}
              required
              className={textareaClass}
            />
          </label>
          <Button type="submit" isLoading={mutation.isPending} disabled={invalid}>
            授予分配
          </Button>
        </fieldset>
      </form>

      {listQuery.isLoading ? (
        <SkeletonList count={2} />
      ) : listQuery.error ? (
        <Notice
          tone="warning"
          title="资源分配暂时无法加载"
          action={
            <Button variant="secondary" onClick={() => listQuery.refetch()}>
              重试
            </Button>
          }
        >
          {describeError(listQuery.error).message}
        </Notice>
      ) : items.length === 0 ? (
        <EmptyState title="还没有资源分配记录" />
      ) : (
        <ul className="space-y-3">
          {items.map((item) => (
            <li key={item.id} className="border border-divider bg-surface p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 space-y-1 text-sm">
                  <p className="text-text-primary">
                    {ASSIGNMENT_CAPABILITY_LABELS[item.capability]} ·{' '}
                    {item.resource_id ? '资料' : '课程'}
                  </p>
                  <p className="break-all text-xs text-text-secondary">
                    用户 {item.user_id} · 对象 {item.resource_id ?? item.course_id}
                  </p>
                  {item.effective_at && (
                    <p className="text-xs text-text-secondary">
                      生效 {formatDateTime(item.effective_at)}
                    </p>
                  )}
                  {item.reason && (
                    <p className="break-words text-text-secondary">理由：{item.reason}</p>
                  )}
                  {item.revoked_at && (
                    <p className="text-text-secondary">
                      已撤销于 {formatDateTime(item.revoked_at)}
                      {item.revoked_reason ? ` · ${item.revoked_reason}` : ''}
                    </p>
                  )}
                </div>
                {!item.revoked_at && (
                  <Button variant="secondary" onClick={() => setRevoking(item)}>
                    撤销
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {revoking && (
        <RevokeModal
          title="撤销资源分配"
          onClose={() => setRevoking(null)}
          onConfirm={(revokeReason) =>
            revokeResourceAssignment(revoking.id, revokeReason).then(() => {
              queryClient.invalidateQueries({ queryKey: ['admin-resource-assignments'] });
            })
          }
        />
      )}
    </section>
  );
}

function RevokeModal({
  title,
  onClose,
  onConfirm,
}: {
  title: string;
  onClose: () => void;
  onConfirm: (reason: string) => Promise<unknown>;
}) {
  const [reason, setReason] = useState('');
  const mutation = useMutation({
    mutationFn: () => onConfirm(reason.trim()),
    onSuccess: () => {
      toast.success('已撤销');
      setReason('');
      onClose();
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  return (
    <Modal isOpen onClose={onClose} title={title}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (!reason.trim()) {
            toast.error('请填写撤销理由');
            return;
          }
          mutation.mutate();
        }}
      >
        <label className="block text-sm text-text-primary">
          撤销理由（必填）
          <textarea
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            rows={3}
            maxLength={255}
            required
            className={textareaClass}
          />
        </label>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose}>
            关闭
          </Button>
          <Button type="submit" variant="danger" isLoading={mutation.isPending} disabled={!reason.trim()}>
            确认撤销
          </Button>
        </div>
      </form>
    </Modal>
  );
}

/** datetime-local 值转 ISO；空值返回 null。 */
function toIso(value: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}
