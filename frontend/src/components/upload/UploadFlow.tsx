'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import toast from 'react-hot-toast';
import { CheckCircle2, RefreshCw, X } from 'lucide-react';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { describeError } from '@/lib/api/client';
import { listCourses, type CourseListItem } from '@/lib/api/catalog';
import {
  completeUpload,
  createResource,
  createUpload,
  getUpload,
  putFileToStorage,
  submitVersion,
} from '@/lib/api/content';
import {
  RESOURCE_TYPE_LABELS,
  RESOURCE_TYPE_ORDER,
  type ResourceType,
  type RightsBasis,
  type ScanStatus,
} from '@/lib/api/types';
import { formatFileSize } from '@/lib/utils';

/** 与后端 ALLOWED_UPLOAD_MIME / MAX_UPLOAD_SIZE_BYTES 保持一致（D06、§16.1）。 */
const ALLOWED_MIME = [
  'application/pdf',
  'image/png',
  'image/jpeg',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation',
];
const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
const RIGHTS_DECLARATION_VERSION = 'v1';

const rightsOptions: { value: RightsBasis; label: string }[] = [
  { value: 'original', label: '我原创的内容' },
  { value: 'authorized', label: '我已获得作者授权' },
  { value: 'open_license', label: '内容带有明确开放许可' },
  { value: 'third_party', label: '来自第三方来源' },
  { value: 'unverified', label: '来源待核实' },
];

const scanLabels: Record<ScanStatus, string> = {
  pending: '等待安全检查',
  running: '安全检查中',
  passed: '检查通过',
  rejected: '检查未通过',
  failed: '检查未完成',
};

type UploadPhase = 'queued' | 'uploading' | 'completing' | 'done' | 'error';

interface FileEntry {
  key: string;
  file: File;
  phase: UploadPhase;
  progress: number;
  uploadId: string | null;
  fileId: string | null;
  scanStatus: ScanStatus | null;
  error: string | null;
}

function newKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `up-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function UploadFlow() {
  const [courseKeyword, setCourseKeyword] = useState('');
  const [course, setCourse] = useState<CourseListItem | null>(null);
  const [resourceType, setResourceType] = useState<ResourceType>('note');
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [changeSummary, setChangeSummary] = useState('');
  const [tagsInput, setTagsInput] = useState('');
  const [previewDisabled, setPreviewDisabled] = useState(false);
  const [rightsBasis, setRightsBasis] = useState<RightsBasis>('original');
  const [rightsSource, setRightsSource] = useState('');
  const [rightsAuthor, setRightsAuthor] = useState('');
  const [rightsLicense, setRightsLicense] = useState('');
  const [created, setCreated] = useState<{ resourceId: string; versionId: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);

  const courseQuery = useQuery({
    queryKey: ['upload-course-search', courseKeyword],
    queryFn: () => listCourses({ q: courseKeyword.trim(), limit: 8 }),
    enabled: courseKeyword.trim().length >= 2,
  });

  const anyScanPending = entries.some(
    (entry) =>
      entry.fileId !== null && entry.scanStatus !== 'passed' && entry.scanStatus !== null,
  );

  const refreshScan = useCallback(async () => {
    const targets = entries.filter(
      (entry) => entry.uploadId && entry.fileId && entry.scanStatus !== 'passed',
    );
    if (targets.length === 0) return;
    const results = await Promise.all(
      targets.map(async (entry) => {
        try {
          const status = await getUpload(entry.uploadId as string);
          return { key: entry.key, scanStatus: status.scan_status, fileId: status.file_id };
        } catch {
          return null;
        }
      }),
    );
    setEntries((prev) =>
      prev.map((entry) => {
        const match = results.find((item) => item && item.key === entry.key);
        if (!match) return entry;
        return {
          ...entry,
          scanStatus: (match.scanStatus ?? entry.scanStatus) as ScanStatus | null,
          fileId: match.fileId ?? entry.fileId,
        };
      }),
    );
  }, [entries]);

  // 检查未完成时允许查看进度：轮询而非展示虚假的可发布状态（§26.3）。
  useEffect(() => {
    if (!anyScanPending) return;
    const timer = window.setInterval(() => {
      void refreshScan();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [anyScanPending, refreshScan]);

  async function handleFiles(fileList: FileList | null) {
    if (!fileList) return;
    const incoming = Array.from(fileList);
    const accepted: FileEntry[] = [];

    for (const file of incoming) {
      if (file.size > MAX_UPLOAD_BYTES) {
        toast.error(`「${file.name}」超过 50 MiB 上限`);
        continue;
      }
      if (file.type && !ALLOWED_MIME.includes(file.type)) {
        toast.error(`「${file.name}」的格式暂不支持（仅 PDF、PNG/JPEG、DOCX/PPTX）`);
        continue;
      }
      accepted.push({
        key: newKey(),
        file,
        phase: 'queued',
        progress: 0,
        uploadId: null,
        fileId: null,
        scanStatus: null,
        error: null,
      });
    }

    if (accepted.length === 0) return;
    setEntries((prev) => [...prev, ...accepted]);

    for (const entry of accepted) {
      await runUpload(entry);
    }
  }

  async function runUpload(entry: FileEntry) {
    const patch = (changes: Partial<FileEntry>) =>
      setEntries((prev) =>
        prev.map((item) => (item.key === entry.key ? { ...item, ...changes } : item)),
      );

    try {
      patch({ phase: 'uploading', progress: 0, error: null });
      const session = await createUpload({
        original_name: entry.file.name,
        declared_mime: entry.file.type || 'application/octet-stream',
        expected_size: entry.file.size,
      });
      patch({ uploadId: session.id });

      await putFileToStorage(session.upload_url, entry.file, session.content_type, (percent) =>
        patch({ progress: percent }),
      );

      patch({ phase: 'completing', progress: 100 });
      const status = await completeUpload(session.id);
      patch({
        phase: 'done',
        fileId: status.file_id,
        scanStatus: status.scan_status,
      });
      toast.success(`「${entry.file.name}」已上传，正在等待安全检查`);
    } catch (error) {
      patch({ phase: 'error', error: describeError(error).message });
      toast.error(`「${entry.file.name}」上传失败：${describeError(error).message}`);
    }
  }

  const uploadableFiles = useMemo(
    () => entries.filter((entry) => entry.fileId && entry.phase === 'done'),
    [entries],
  );

  const canSave =
    !!course && title.trim().length > 0 && uploadableFiles.length > 0 && !!rightsBasis;
  const allScansPassed =
    uploadableFiles.length > 0 &&
    uploadableFiles.every((entry) => entry.scanStatus === 'passed');

  async function handleSaveDraft() {
    if (!course) {
      toast.error('请先选择课程');
      return;
    }
    if (!title.trim()) {
      toast.error('请填写资料标题');
      return;
    }
    if (uploadableFiles.length === 0) {
      toast.error('请至少上传一个文件并等待上传完成');
      return;
    }
    setSaving(true);
    try {
      const result = await createResource(
        {
          course_id: course.id,
          resource_type: resourceType,
          title: title.trim(),
          description: description.trim() || null,
          change_summary: changeSummary.trim() || null,
          preview_disabled: previewDisabled,
          files: uploadableFiles.map((entry, index) => ({
            file_id: entry.fileId as string,
            purpose: 'primary',
            display_order: index,
          })),
          rights: {
            rights_basis: rightsBasis,
            source: rightsSource.trim() || null,
            original_author: rightsAuthor.trim() || null,
            license: rightsLicense.trim() || null,
            declaration_text_version: RIGHTS_DECLARATION_VERSION,
          },
          tags: tagsInput
            .split(',')
            .map((tag) => tag.trim())
            .filter(Boolean),
        },
        newKey(),
      );
      setCreated({ resourceId: result.resource_id, versionId: result.version_id });
      toast.success('草稿已保存');
    } catch (error) {
      toast.error(describeError(error).message);
    } finally {
      setSaving(false);
    }
  }

  async function handleSubmit() {
    if (!created) return;
    setSubmitting(true);
    try {
      await submitVersion(created.resourceId, created.versionId, newKey());
      toast.success('已提交审核，结果会通过站内通知告知');
      setCreated(null);
      setEntries([]);
      setTitle('');
      setDescription('');
      setChangeSummary('');
      setTagsInput('');
    } catch (error) {
      toast.error(describeError(error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-8">
      {/* 步骤 1：课程与类型 */}
      <StepBlock index={1} title="选择课程与资料类型" tone="bg-brand">
        {course ? (
          <div className="flex flex-wrap items-center justify-between gap-3 border border-divider bg-surface p-4">
            <div>
              <p className="font-medium text-text-primary">
                {course.name}
                {course.code ? `（${course.code}）` : ''}
              </p>
              <p className="text-sm text-text-secondary">
                {course.college_name ?? '归属学院待确认'}
              </p>
            </div>
            <Button variant="secondary" onClick={() => setCourse(null)}>
              重新选择
            </Button>
          </div>
        ) : (
          <div className="space-y-3">
            <label htmlFor="course-search" className="block text-sm font-medium text-text-primary">
              搜索课程（至少输入 2 个字符）
            </label>
            <input
              id="course-search"
              value={courseKeyword}
              onChange={(event) => setCourseKeyword(event.target.value)}
              placeholder="课程名、代码或简称"
              className="min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary placeholder:text-text-secondary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
            />
            {courseQuery.isFetching && (
              <p className="text-sm text-text-secondary">正在查找课程…</p>
            )}
            {courseQuery.error && (
              <p className="text-sm text-text-secondary">
                课程搜索暂时不可用：{describeError(courseQuery.error).message}
              </p>
            )}
            {courseQuery.data && courseQuery.data.items.length > 0 && (
              <ul className="divide-y divide-divider border border-divider">
                {courseQuery.data.items.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => setCourse(item)}
                      className="flex min-h-11 w-full items-center justify-between px-4 text-left hover:bg-canvas"
                    >
                      <span className="text-sm text-text-primary">
                        {item.name}
                        {item.code ? ` · ${item.code}` : ''}
                      </span>
                      <span className="text-xs text-text-secondary">
                        {item.college_name ?? '归属待确认'}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {courseQuery.data && courseQuery.data.items.length === 0 && (
              <p className="text-sm text-text-secondary">没有匹配的课程，换个关键词再试。</p>
            )}
          </div>
        )}

        <div className="mt-4">
          <label htmlFor="resource-type" className="mb-1 block text-sm font-medium text-text-primary">
            资料类型
          </label>
          <select
            id="resource-type"
            value={resourceType}
            onChange={(event) => setResourceType(event.target.value as ResourceType)}
            className="min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus md:max-w-xs"
          >
            {RESOURCE_TYPE_ORDER.map((value) => (
              <option key={value} value={value}>
                {RESOURCE_TYPE_LABELS[value]}
              </option>
            ))}
          </select>
        </div>
      </StepBlock>

      {/* 步骤 2：文件上传（上传会话 → 直传 → 完成） */}
      <StepBlock index={2} title="上传文件" tone="bg-cyan">
        <p className="text-sm text-text-secondary">
          支持 PDF、PNG/JPEG、DOCX/PPTX，单文件不超过 50 MiB。文件先进入私有隔离区，通过安全检查后才可用于发布。
        </p>
        <div className="mt-3">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ALLOWED_MIME.join(',')}
            onChange={(event) => {
              void handleFiles(event.target.files);
              event.target.value = '';
            }}
            className="block w-full text-sm text-text-secondary file:mr-3 file:min-h-11 file:border-0 file:bg-ink file:px-4 file:text-sm file:font-medium file:text-on-ink hover:file:bg-ink/90"
          />
        </div>

        {entries.length > 0 && (
          <ul className="mt-4 space-y-3">
            {entries.map((entry) => (
              <li key={entry.key} className="border border-divider bg-surface p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-text-primary">
                      {entry.file.name}
                    </p>
                    <p className="text-xs text-text-secondary">
                      {formatFileSize(entry.file.size)}
                      {entry.scanStatus ? ` · ${scanLabels[entry.scanStatus]}` : ''}
                    </p>
                  </div>
                  <button
                    type="button"
                    aria-label={`移除 ${entry.file.name}`}
                    onClick={() =>
                      setEntries((prev) => prev.filter((item) => item.key !== entry.key))
                    }
                    className="flex h-11 w-11 shrink-0 items-center justify-center text-text-secondary hover:bg-canvas"
                  >
                    <X className="h-4 w-4" aria-hidden="true" />
                  </button>
                </div>

                {entry.phase !== 'error' && entry.phase !== 'done' && (
                  <div className="mt-3">
                    <div
                      role="progressbar"
                      aria-valuenow={entry.progress}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      className="h-2 w-full bg-divider"
                    >
                      <div className="h-2 bg-brand" style={{ width: `${entry.progress}%` }} />
                    </div>
                    <p className="mt-1 text-xs text-text-secondary">
                      {entry.phase === 'completing' ? '正在核验上传…' : `上传中 ${entry.progress}%`}
                    </p>
                  </div>
                )}

                {entry.phase === 'error' && (
                  <p className="mt-2 text-sm text-text-primary">
                    <span className="text-crimson">上传失败：</span>
                    {entry.error}
                  </p>
                )}

                {entry.phase === 'done' && entry.scanStatus !== 'passed' && (
                  <p className="mt-2 text-xs text-text-secondary">
                    安全检查完成前不能提交审核，可稍后刷新查看进度。
                  </p>
                )}
                {entry.phase === 'done' && entry.scanStatus === 'passed' && (
                  <p className="mt-2 flex items-center gap-1 text-xs text-text-primary">
                    <CheckCircle2 className="h-3.5 w-3.5 text-emerald" aria-hidden="true" />
                    检查通过
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}

        {anyScanPending && (
          <div className="mt-3">
            <Button variant="secondary" onClick={() => void refreshScan()}>
              <RefreshCw className="h-4 w-4" aria-hidden="true" />
              刷新检查状态
            </Button>
          </div>
        )}
      </StepBlock>

      {/* 步骤 3：内容说明与来源声明 */}
      <StepBlock index={3} title="填写说明与来源声明" tone="bg-violet">
        <div className="grid gap-4">
          <div>
            <label htmlFor="upload-title" className="mb-1 block text-sm font-medium text-text-primary">
              资料标题（必填）
            </label>
            <input
              id="upload-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              maxLength={300}
              className="min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
            />
          </div>
          <div>
            <label htmlFor="upload-desc" className="mb-1 block text-sm font-medium text-text-primary">
              内容说明
            </label>
            <textarea
              id="upload-desc"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={3}
              maxLength={5000}
              className="w-full border border-control-border bg-surface px-3 py-2 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
            />
          </div>
          <div>
            <label htmlFor="upload-change" className="mb-1 block text-sm font-medium text-text-primary">
              版本变更说明（可选）
            </label>
            <input
              id="upload-change"
              value={changeSummary}
              onChange={(event) => setChangeSummary(event.target.value)}
              maxLength={2000}
              className="min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
            />
          </div>
          <div>
            <label htmlFor="upload-tags" className="mb-1 block text-sm font-medium text-text-primary">
              标签（用逗号分隔，可选）
            </label>
            <input
              id="upload-tags"
              value={tagsInput}
              onChange={(event) => setTagsInput(event.target.value)}
              className="min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
            />
          </div>

          <fieldset className="border border-divider p-4">
            <legend className="px-2 text-sm font-medium text-text-primary">来源声明（必填）</legend>
            <div className="space-y-3">
              <div>
                <label htmlFor="rights-basis" className="mb-1 block text-sm text-text-secondary">
                  权利依据
                </label>
                <select
                  id="rights-basis"
                  value={rightsBasis}
                  onChange={(event) => setRightsBasis(event.target.value as RightsBasis)}
                  className="min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
                >
                  {rightsOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label htmlFor="rights-source" className="mb-1 block text-sm text-text-secondary">
                    来源说明
                  </label>
                  <input
                    id="rights-source"
                    value={rightsSource}
                    onChange={(event) => setRightsSource(event.target.value)}
                    maxLength={2000}
                    className="min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
                  />
                </div>
                <div>
                  <label htmlFor="rights-author" className="mb-1 block text-sm text-text-secondary">
                    原作者（如适用）
                  </label>
                  <input
                    id="rights-author"
                    value={rightsAuthor}
                    onChange={(event) => setRightsAuthor(event.target.value)}
                    maxLength={300}
                    className="min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
                  />
                </div>
              </div>
              <div>
                <label htmlFor="rights-license" className="mb-1 block text-sm text-text-secondary">
                  许可（如适用）
                </label>
                <input
                  id="rights-license"
                  value={rightsLicense}
                  onChange={(event) => setRightsLicense(event.target.value)}
                  maxLength={300}
                  className="min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
                />
              </div>
              <label className="flex items-start gap-2 text-sm text-text-secondary">
                <input
                  type="checkbox"
                  checked={previewDisabled}
                  onChange={(event) => setPreviewDisabled(event.target.checked)}
                  className="mt-1 h-4 w-4 accent-brand"
                />
                关闭公开预览（适用于整份资料较短、预览可能等同全文的情况）
              </label>
            </div>
          </fieldset>
        </div>
      </StepBlock>

      {/* 步骤 4：保存草稿 / 提交审核（不同动作，文案准确） */}
      <StepBlock index={4} title="保存或提交" tone="bg-amber">
        <Notice tone="info" title="保存草稿与提交审核是两个不同动作">
          <ul className="space-y-1">
            <li>· 保存草稿：内容可以继续修改，不对其他用户可见。</li>
            <li>· 提交审核：冻结当前版本快照交给审核员；通过后才会发布。</li>
          </ul>
        </Notice>

        <div className="mt-4 flex flex-wrap gap-3">
          <Button variant="secondary" onClick={handleSaveDraft} isLoading={saving} disabled={!canSave}>
            保存草稿
          </Button>
          <Button
            variant="primary"
            onClick={handleSubmit}
            isLoading={submitting}
            disabled={!created || !allScansPassed}
          >
            提交审核
          </Button>
        </div>
        {!created && (
          <p className="mt-2 text-xs text-text-secondary">
            请先保存草稿；保存成功后才能提交审核。
          </p>
        )}
        {created && !allScansPassed && (
          <p className="mt-2 text-xs text-text-secondary">
            仍有文件未通过安全检查，暂时不能提交审核。草稿已保存，可以稍后再来提交。
          </p>
        )}
        {created && (
          <p className="mt-2 text-xs text-text-secondary">
            草稿已保存（版本 {created.versionId.slice(0, 8)}…）。可在
            <Link href="/me/resources" className="mx-1 underline underline-offset-2">
              我的资料
            </Link>
            继续编辑。
          </p>
        )}
      </StepBlock>
    </div>
  );
}

function StepBlock({
  index,
  title,
  tone,
  children,
}: {
  index: number;
  title: string;
  tone: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border border-divider bg-surface">
      <header className={`flex items-center gap-3 ${tone} px-4 py-3 text-on-color`}>
        <span className="grid h-8 w-8 place-items-center bg-on-color/20 text-sm font-semibold">
          {index}
        </span>
        <h2 className="text-lg font-semibold">{title}</h2>
      </header>
      <div className="p-4 md:p-6">{children}</div>
    </section>
  );
}
