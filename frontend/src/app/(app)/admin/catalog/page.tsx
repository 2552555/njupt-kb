'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { SkeletonList } from '@/components/ui/Skeleton';
import { describeError } from '@/lib/api/client';
import {
  createCatalog,
  deactivateCatalog,
  getCatalogMergeImpact,
  listCatalog,
  mergeCatalog,
  renameCatalog,
  type CatalogCreateIn,
  type CatalogEntityType,
  type CatalogItem,
  type CatalogMergeImpact,
} from '@/lib/api/admin';
import { hasPermission, useAuthStore } from '@/stores/authStore';

const TABS: { value: CatalogEntityType; label: string; nameLabel: string }[] = [
  { value: 'university', label: '大学', nameLabel: '学校名称' },
  { value: 'college', label: '学院', nameLabel: '学院名称' },
  { value: 'major', label: '专业', nameLabel: '专业名称' },
  { value: 'course', label: '课程', nameLabel: '课程名称' },
  { value: 'teacher', label: '教师', nameLabel: '教师姓名' },
  { value: 'semester', label: '学期', nameLabel: '学年（如 2024-2025）' },
];

const STATUS_LABELS: Record<string, string> = {
  active: '正常',
  inactive: '已停用',
  merged: '已合并',
  deprecated: '已弃用',
  pending_classification: '待归类',
};

const STATUS_TONES: Record<string, string> = {
  active: 'bg-emerald text-on-color',
  inactive: 'bg-steel text-on-color',
  merged: 'bg-amber text-on-color',
  deprecated: 'bg-steel text-on-color',
  pending_classification: 'bg-amber text-on-color',
};

const SEASONS = [
  { value: 'spring', label: '春季' },
  { value: 'fall', label: '秋季' },
  { value: 'summer', label: '夏季' },
];

export default function AdminCatalogPage() {
  const user = useAuthStore((s) => s.user);
  return hasPermission(user, 'catalog.manage') ? <Catalog /> : <Notice title="需要管理员权限" />;
}

function Catalog() {
  const queryClient = useQueryClient();
  const [entityType, setEntityType] = useState<CatalogEntityType>('course');
  const [createOpen, setCreateOpen] = useState(false);
  const [renameTarget, setRenameTarget] = useState<CatalogItem | null>(null);
  const [deactivateTarget, setDeactivateTarget] = useState<CatalogItem | null>(null);
  const [mergeSource, setMergeSource] = useState<CatalogItem | null>(null);

  const tab = TABS.find((item) => item.value === entityType)!;

  const listQuery = useQuery({
    queryKey: ['admin-catalog', entityType],
    queryFn: () => listCatalog(entityType),
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['admin-catalog'] });

  return (
    <div className="space-y-6">
      <p className="max-w-2xl text-sm text-text-secondary">
        停用是软停用，已关联资料仍可正常读取；合并把源项引用归并到目标项。改名与合并会写入审计日志。
      </p>

      <div className="flex flex-wrap gap-1">
        {TABS.map((item) => (
          <button
            key={item.value}
            type="button"
            aria-pressed={entityType === item.value}
            onClick={() => setEntityType(item.value)}
            className={`inline-flex min-h-11 items-center px-4 text-sm font-medium ${
              entityType === item.value
                ? 'bg-brand text-on-color'
                : 'border border-control-border text-text-primary hover:bg-canvas'
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-text-secondary">
          共 {listQuery.data?.length ?? 0} 项
        </p>
        <Button onClick={() => setCreateOpen(true)}>新建{tab.label}</Button>
      </div>

      {listQuery.isLoading ? (
        <SkeletonList count={3} />
      ) : listQuery.error ? (
        <Notice
          tone="warning"
          title="目录列表暂时无法加载"
          action={
            <Button variant="secondary" onClick={() => listQuery.refetch()}>
              重试
            </Button>
          }
        >
          {describeError(listQuery.error).message}
        </Notice>
      ) : (listQuery.data?.length ?? 0) === 0 ? (
        <Notice tone="info" title={`暂无${tab.label}`}>
          点击「新建{tab.label}」创建第一个目录项。
        </Notice>
      ) : (
        <ul className="space-y-3">
          {listQuery.data?.map((item) => (
            <li key={item.id} className="border border-divider bg-surface p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-medium text-text-primary">
                    <span
                      className={`mr-2 px-2 py-0.5 text-xs font-semibold ${
                        STATUS_TONES[item.status] ?? 'bg-steel text-on-color'
                      }`}
                    >
                      {STATUS_LABELS[item.status] ?? item.status}
                    </span>
                    {item.name}
                    {item.code && (
                      <span className="ml-2 text-xs text-text-secondary">
                        {item.code}
                      </span>
                    )}
                  </p>
                  <p className="mt-1 text-xs text-text-secondary">
                    {item.parent_name ? `${item.parent_name} · ` : ''}
                    {item.academic_year
                      ? `${item.academic_year} ${SEASONS.find((s) => s.value === item.season)?.label ?? ''}`
                      : ''}
                    ID {item.id.slice(0, 8)}…
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => setRenameTarget(item)}
                  >
                    改名
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={item.status !== 'active'}
                    onClick={() => setMergeSource(item)}
                  >
                    合并
                  </Button>
                  <Button
                    variant="danger"
                    size="sm"
                    disabled={item.status !== 'active'}
                    onClick={() => setDeactivateTarget(item)}
                  >
                    停用
                  </Button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}

      {createOpen && (
        <CreateModal
          entityType={entityType}
          nameLabel={tab.nameLabel}
          onClose={() => setCreateOpen(false)}
          onCreated={() => {
            refresh();
            setCreateOpen(false);
          }}
        />
      )}

      {renameTarget && (
        <RenameModal
          entityType={entityType}
          item={renameTarget}
          nameLabel={tab.nameLabel}
          onClose={() => setRenameTarget(null)}
          onRenamed={() => {
            refresh();
            setRenameTarget(null);
          }}
        />
      )}

      {deactivateTarget && (
        <DeactivateModal
          entityType={entityType}
          item={deactivateTarget}
          onClose={() => setDeactivateTarget(null)}
          onDone={() => {
            refresh();
            setDeactivateTarget(null);
          }}
        />
      )}

      {mergeSource && listQuery.data && (
        <MergeModal
          entityType={entityType}
          source={mergeSource}
          candidates={listQuery.data.filter(
            (item) => item.id !== mergeSource.id && item.status === 'active',
          )}
          onClose={() => setMergeSource(null)}
          onDone={() => {
            refresh();
            setMergeSource(null);
          }}
        />
      )}
    </div>
  );
}

function CreateModal({
  entityType,
  nameLabel,
  onClose,
  onCreated,
}: {
  entityType: CatalogEntityType;
  nameLabel: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [form, setForm] = useState<CatalogCreateIn>({ name: '' });
  const needsUniversity = ['college', 'course', 'teacher', 'semester'].includes(
    entityType,
  );
  const needsCollege = ['major', 'course'].includes(entityType);

  const universities = useQuery({
    queryKey: ['admin-catalog', 'university'],
    queryFn: () => listCatalog('university'),
    enabled: needsUniversity,
  });
  const colleges = useQuery({
    queryKey: ['admin-catalog', 'college'],
    queryFn: () => listCatalog('college'),
    enabled: needsCollege,
  });

  const mutation = useMutation({
    mutationFn: () => createCatalog(entityType, form),
    onSuccess: () => {
      toast.success('已创建');
      onCreated();
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const set = (patch: Partial<CatalogCreateIn>) =>
    setForm((prev) => ({ ...prev, ...patch }));

  const missingParent =
    (needsUniversity && !form.university_id) ||
    (entityType === 'major' && !form.college_id) ||
    (entityType === 'semester' && !form.season);

  return (
    <Modal isOpen onClose={onClose} title={`新建${nameLabel}`}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          mutation.mutate();
        }}
      >
        <Input
          label={nameLabel}
          value={form.name}
          onChange={(event) => set({ name: event.target.value })}
        />

        {entityType === 'university' && (
          <Input
            label="slug（唯一标识）"
            value={form.slug ?? ''}
            onChange={(event) => set({ slug: event.target.value })}
          />
        )}

        {['college', 'major', 'course'].includes(entityType) && (
          <Input
            label="代码（可选）"
            value={form.code ?? ''}
            onChange={(event) => set({ code: event.target.value })}
          />
        )}

        {entityType === 'college' && (
          <Input
            label="简称（可选）"
            value={form.short_name ?? ''}
            onChange={(event) => set({ short_name: event.target.value })}
          />
        )}

        {needsUniversity && (
          <label className="block text-sm font-medium text-text-primary">
            所属学校
            <select
              value={form.university_id ?? ''}
              onChange={(event) => set({ university_id: event.target.value })}
              className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
            >
              <option value="">请选择</option>
              {universities.data?.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
        )}

        {needsCollege && (
          <label className="block text-sm font-medium text-text-primary">
            所属学院{entityType === 'course' ? '（可选）' : ''}
            <select
              value={form.college_id ?? ''}
              onChange={(event) => set({ college_id: event.target.value })}
              className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
            >
              <option value="">{entityType === 'course' ? '不指定' : '请选择'}</option>
              {colleges.data?.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
        )}

        {entityType === 'semester' && (
          <label className="block text-sm font-medium text-text-primary">
            季节
            <select
              value={form.season ?? ''}
              onChange={(event) => set({ season: event.target.value })}
              className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
            >
              <option value="">请选择</option>
              {SEASONS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
        )}

        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button
            type="submit"
            isLoading={mutation.isPending}
            disabled={!form.name.trim() || missingParent}
          >
            创建
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function RenameModal({
  entityType,
  item,
  nameLabel,
  onClose,
  onRenamed,
}: {
  entityType: CatalogEntityType;
  item: CatalogItem;
  nameLabel: string;
  onClose: () => void;
  onRenamed: () => void;
}) {
  const [name, setName] = useState(item.name);
  const [reason, setReason] = useState('');

  const mutation = useMutation({
    mutationFn: () => renameCatalog(entityType, item.id, name.trim(), reason.trim()),
    onSuccess: () => {
      toast.success('已改名');
      onRenamed();
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  return (
    <Modal isOpen onClose={onClose} title={`改名 · ${item.name}`}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          mutation.mutate();
        }}
      >
        <Input
          label={nameLabel}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <label className="block text-sm font-medium text-text-primary">
          改名理由（可选）
          <textarea
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            rows={2}
            maxLength={500}
            className="mt-1 w-full border border-control-border bg-surface px-3 py-2 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
          />
        </label>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button
            type="submit"
            isLoading={mutation.isPending}
            disabled={!name.trim() || name.trim() === item.name}
          >
            保存
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function DeactivateModal({
  entityType,
  item,
  onClose,
  onDone,
}: {
  entityType: CatalogEntityType;
  item: CatalogItem;
  onClose: () => void;
  onDone: () => void;
}) {
  const [reason, setReason] = useState('');
  const mutation = useMutation({
    mutationFn: () => deactivateCatalog(entityType, item.id, reason.trim()),
    onSuccess: () => {
      toast.success('已停用');
      onDone();
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  return (
    <Modal isOpen onClose={onClose} title={`停用 · ${item.name}`}>
      <div className="space-y-4">
        <Notice tone="warning" title="确认停用？">
          停用后该项不再出现在前台选择器与搜索中，但已关联的资料仍可正常读取。
          这是软停用，不会物理删除。
        </Notice>
        <label className="block text-sm font-medium text-text-primary">
          停用理由（可选）
          <textarea
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            rows={2}
            maxLength={500}
            className="mt-1 w-full border border-control-border bg-surface px-3 py-2 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
          />
        </label>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="danger"
            isLoading={mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            确认停用
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function MergeModal({
  entityType,
  source,
  candidates,
  onClose,
  onDone,
}: {
  entityType: CatalogEntityType;
  source: CatalogItem;
  candidates: CatalogItem[];
  onClose: () => void;
  onDone: () => void;
}) {
  const [targetId, setTargetId] = useState('');
  const [reason, setReason] = useState('');
  const [impact, setImpact] = useState<CatalogMergeImpact | null>(null);

  const preview = useMutation({
    mutationFn: () => getCatalogMergeImpact(entityType, source.id, targetId),
    onSuccess: (data) => setImpact(data),
    onError: (error) => toast.error(describeError(error).message),
  });

  const merge = useMutation({
    mutationFn: () => mergeCatalog(entityType, source.id, targetId, reason.trim()),
    onSuccess: (data) => {
      toast.success(`已合并，影响 ${data.total} 条引用`);
      onDone();
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  return (
    <Modal isOpen onClose={onClose} title={`合并 · ${source.name}`}>
      <div className="space-y-4">
        <p className="text-sm text-text-secondary">
          把「{source.name}」归并到目标项：引用该记录的资料/课程会被改写到目标项，源项标记为
          已合并并保留影响面记录。
        </p>

        <label className="block text-sm font-medium text-text-primary">
          目标项
          <select
            value={targetId}
            onChange={(event) => {
              setTargetId(event.target.value);
              setImpact(null);
            }}
            className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
          >
            <option value="">请选择</option>
            {candidates.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>

        {!impact ? (
          <div className="flex justify-end">
            <Button
              variant="secondary"
              isLoading={preview.isPending}
              disabled={!targetId}
              onClick={() => preview.mutate()}
            >
              预览影响面
            </Button>
          </div>
        ) : (
          <div className="space-y-3 border border-divider p-3">
            <p className="text-sm font-medium text-text-primary">
              影响面：共 {impact.total} 条引用
            </p>
            {impact.total === 0 ? (
              <p className="text-xs text-text-secondary">没有引用该记录的条目。</p>
            ) : (
              <ul className="space-y-1 text-xs text-text-secondary">
                {impact.references
                  .filter((ref) => ref.count > 0)
                  .map((ref) => (
                    <li key={`${ref.table}.${ref.column}`}>
                      {ref.label}：{ref.count} 条
                    </li>
                  ))}
              </ul>
            )}
            <label className="block text-sm font-medium text-text-primary">
              合并理由（必填）
              <textarea
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                rows={2}
                maxLength={500}
                className="mt-1 w-full border border-control-border bg-surface px-3 py-2 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
              />
            </label>
          </div>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="danger"
            isLoading={merge.isPending}
            disabled={!impact || !reason.trim()}
            onClick={() => merge.mutate()}
          >
            确认合并
          </Button>
        </div>
      </div>
    </Modal>
  );
}
