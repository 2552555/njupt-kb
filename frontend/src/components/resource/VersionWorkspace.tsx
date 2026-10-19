'use client';

import Link from 'next/link';
import { useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { createPreviewGrant } from '@/lib/api/access';
import { describeError } from '@/lib/api/client';
import { createVersion, getVersionDetail, listVersions, submitVersion, updateVersion,
  withdrawResource, createUpload, completeUpload, putFileToStorage, inspectFile, type VersionDetail } from '@/lib/api/content';
import type { RightsBasis } from '@/lib/api/types';

const reviewLabels = { draft: '草稿', pending: '审核中', needs_info: '需要补证', approved: '已通过', rejected: '已驳回' };
const scanLabels = { pending: '等待检查', running: '检查中', passed: '检查通过', rejected: '检查拒绝', failed: '检查失败' };

export function VersionWorkspace({ resourceId, versionId }: { resourceId: string; versionId: string }) {
  const query = useQuery({ queryKey: ['version', resourceId, versionId],
    queryFn: () => getVersionDetail(resourceId, versionId), refetchOnWindowFocus: false });
  const versions = useQuery({ queryKey: ['versions', resourceId], queryFn: () => listVersions(resourceId) });
  if (query.isPending) return <p role="status">正在加载版本…</p>;
  if (query.error) return <Notice title="无法读取这个版本" tone="warning"
    action={<Button onClick={() => query.refetch()}>重试</Button>}>{describeError(query.error).message}</Notice>;
  return <div className="space-y-6">
    <Link href="/me/resources" className="text-sm underline">返回我的资料</Link>
    <nav aria-label="历史版本" className="flex flex-wrap gap-3">{versions.data?.map(version =>
      <Link key={version.id} href={`/me/resources/${resourceId}/versions/${version.id}`}
        aria-current={version.id === versionId ? 'page' : undefined} className="text-sm underline">V{version.version_no} · {reviewLabels[version.review_status]}</Link>)}</nav>
    <SnapshotEditor key={`${versionId}:${query.data.version.revision}`} detail={query.data} />
  </div>;
}

function SnapshotEditor({ detail }: { detail: VersionDetail }) {
  const router = useRouter();
  const client = useQueryClient();
  const version = detail.version;
  const editable = detail.can_edit && version.review_status === 'draft';
  const [title, setTitle] = useState(version.title);
  const [description, setDescription] = useState(version.description ?? '');
  const [summary, setSummary] = useState(version.change_summary ?? '');
  const [previewDisabled, setPreviewDisabled] = useState(version.preview_disabled);
  const [rights, setRights] = useState(detail.rights ?? { rights_basis: 'original' as RightsBasis, declaration_text_version: 'v1', source: '', original_author: '', license: '' });
  const [files, setFiles] = useState(detail.files);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [withdrawReason, setWithdrawReason] = useState('');
  const [saved, setSaved] = useState(true);
  const savedRevision = useRef(version.revision);
  const submitKey = useRef<string | null>(null);

  async function saveChanges() {
    const updated = await updateVersion(detail.resource_id, version.id, { ...payload(), revision: savedRevision.current });
    savedRevision.current = updated.revision;
    setSaved(true);
  }

  async function act(action: () => Promise<void>) {
    setBusy(true); setError('');
    try { await action(); }
    catch (failure) { setError(describeError(failure).message); }
    finally { setBusy(false); }
  }
  function payload() {
    return { title, description, change_summary: summary, preview_disabled: previewDisabled, rights,
      files: files.map((file, index) => ({ file_id: file.id, purpose: index === 0 ? 'primary' as const : 'attachment' as const, display_order: index })), tags: detail.tags };
  }
  async function refresh() {
    await client.invalidateQueries({ queryKey: ['version', detail.resource_id, version.id] });
    await client.invalidateQueries({ queryKey: ['my-resources'] });
    await client.invalidateQueries({ queryKey: ['versions', detail.resource_id] });
  }
  const field = 'w-full border border-control-border bg-surface p-3 text-sm text-text-primary';
  return <section className="space-y-5">
    <header><h2 className="dn-display text-2xl">{version.title} · V{version.version_no}</h2>
      {!editable && detail.can_edit && <p className="mt-2 text-sm text-text-secondary">这个快照已冻结，需要修改时请创建新修订。</p>}</header>
    {detail.review_reason && <Notice title="最近审核意见" tone={version.review_status === 'rejected' ? 'warning' : 'info'}>{detail.review_reason}</Notice>}
    {error && <Notice title="操作未完成" tone="warning">{error}</Notice>}
    <fieldset disabled={busy || !editable} className="space-y-4" onChange={() => setSaved(false)}>
      <label className="block">标题<input value={title} onChange={event => setTitle(event.target.value)} className={field} maxLength={300} /></label>
      <label className="block">资料说明<textarea value={description} onChange={event => setDescription(event.target.value)} className={field} rows={4} maxLength={5000} /></label>
      <label className="block">本次修改<textarea value={summary} onChange={event => setSummary(event.target.value)} className={field} maxLength={2000} /></label>
      <label className="flex gap-2"><input type="checkbox" checked={previewDisabled} onChange={event => setPreviewDisabled(event.target.checked)} />不对普通用户提供预览</label>
      <label className="block">来源类型<select className={field} value={rights.rights_basis} onChange={event => setRights({ ...rights, rights_basis: event.target.value as RightsBasis })}>
        <option value="original">原创</option><option value="authorized">已获授权</option><option value="open_license">开放许可</option><option value="third_party">第三方来源</option><option value="unverified">待核实</option>
      </select></label>
      <label className="block">来源地址或说明<input className={field} value={rights.source ?? ''} onChange={event => setRights({ ...rights, source: event.target.value })} maxLength={2000} /></label>
      <label className="block">原作者<input className={field} value={rights.original_author ?? ''} onChange={event => setRights({ ...rights, original_author: event.target.value })} maxLength={300} /></label>
      <label className="block">授权或许可<input className={field} value={rights.license ?? ''} onChange={event => setRights({ ...rights, license: event.target.value })} maxLength={300} /></label>
    </fieldset>
    <ul className="space-y-2">{files.map((file, index) => <li key={file.id} className="flex flex-wrap items-center gap-3 border border-divider p-3">
      <span className="min-w-0 flex-1 break-all">{index === 0 ? '主文件' : '附件'}：{file.original_name} · {scanLabels[file.scan_status]}</span>
      <Button variant="secondary" disabled={busy || file.scan_status !== 'passed'} onClick={() => act(async () => {
        const grant = await inspectFile(detail.resource_id, version.id, file.id);
        window.location.assign(grant.url);
      })}>查看原件</Button>
      {editable && <Button variant="ghost" disabled={busy} onClick={() => { setFiles(files.filter(item => item.id !== file.id)); setSaved(false); }}>移除</Button>}
    </li>)}</ul>
    {editable && <label className="block text-sm">添加文件（第一个文件为主文件，其余为附件）
      <input type="file" accept=".pdf,.png,.jpg,.jpeg,.docx,.pptx" disabled={busy} className={field} onChange={event => {
        const file = event.target.files?.[0]; event.target.value = ''; if (!file) return;
        if (file.size > 50 * 1024 * 1024) { setError('文件不能超过 50 MiB'); return; }
        void act(async () => {
          const upload = await createUpload({ original_name: file.name, declared_mime: file.type || 'application/octet-stream', expected_size: file.size });
          await putFileToStorage(upload.upload_url, file, upload.content_type);
          const status = await completeUpload(upload.id);
          if (!status.file_id) throw new Error('上传尚未完成，请重试');
          setFiles(current => [...current, { id: status.file_id!, original_name: file.name, size: file.size, detected_mime: null, sha256: null, page_count: null, scan_status: status.scan_status ?? 'pending', purpose: 'attachment', display_order: current.length, created_at: status.created_at }]);
          setSaved(false); toast.success('已接收文件，请保存草稿，待安全检查与预览处理完成后提交');
        });
      }} />
    </label>}
    <div className="flex flex-wrap gap-3">
      {editable ? <>
        <Button disabled={busy || !title.trim() || !files.length} onClick={() => act(async () => {
          await saveChanges(); await refresh(); toast.success('草稿已保存');
        })}>保存草稿</Button>
        <Button variant="primary" disabled={busy || !title.trim() || !files.length || files.some(file => file.scan_status !== 'passed')} onClick={() => act(async () => {
          if (!saved) await saveChanges();
          submitKey.current ??= crypto.randomUUID();
          await submitVersion(detail.resource_id, version.id, submitKey.current);
          await refresh(); toast.success('已提交审核');
        })}>提交审核</Button>
      </> : detail.can_edit && <Button disabled={busy} onClick={() => act(async () => {
        const next = await createVersion(detail.resource_id, payload(), crypto.randomUUID());
        router.push(`/me/resources/${detail.resource_id}/versions/${next.id}`);
        await client.invalidateQueries({ queryKey: ['my-resources'] });
      })}>创建修订</Button>}
      <Button variant="secondary" disabled={busy || !saved} onClick={() => act(refresh)}>刷新处理状态</Button>
      {!version.preview_disabled && <Button variant="secondary" disabled={busy} onClick={() => act(async () => {
        const grant = await createPreviewGrant(detail.resource_id, version.id); window.location.assign(grant.preview_url);
      })}>查看预览</Button>}
    </div>
    {editable && <p className="text-sm text-text-secondary">提交审核会先保存当前修改。文件必须通过安全检查；未就绪时可先保存草稿。</p>}
    {detail.publication_status === 'published' && detail.can_edit && <div className="space-y-2 border-t border-divider pt-5">
      <label className="block text-sm">撤回理由<input className={field} value={withdrawReason} onChange={event => setWithdrawReason(event.target.value)} maxLength={1000} /></label>
      <Button variant="danger" disabled={busy || !withdrawReason.trim()} onClick={() => {
        if (!window.confirm('撤回后将停止新的资料访问授权，确认撤回？')) return;
        void act(async () => { await withdrawResource(detail.resource_id, { reason: withdrawReason, revision: detail.resource_revision }, crypto.randomUUID()); await refresh(); toast.success('资料已撤回'); });
      }}>撤回资料</Button>
    </div>}
  </section>;
}
