'use client';

import Link from 'next/link';
import { useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { CoursePicker } from '@/components/catalog/CoursePicker';
import { Button } from '@/components/ui/Button';
import { Notice } from '@/components/common/Notice';
import { describeError } from '@/lib/api/client';
import { classifyFiles, createImportDraft } from '@/lib/api/imports';
import { completeUpload, createUpload, getUpload, putFileToStorage, submitVersion, type ResourceCreatedOut, type UploadCreateOut, type UploadStatusOut } from '@/lib/api/content';
import { RESOURCE_TYPE_LABELS, RESOURCE_TYPE_ORDER, type ResourceType, type RightsBasis } from '@/lib/api/types';
import { isAdmin, useAuthStore } from '@/stores/authStore';

const mimeByExtension: Record<string, string> = { pdf: 'application/pdf', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation' };
interface Row {
  id: string; file: File; title: string; course: string; type: ResourceType; reason: string;
  confirmed: boolean; rights: RightsBasis; source: string; author: string; license: string;
  upload?: UploadCreateOut; transferred?: boolean; status?: UploadStatusOut;
  resource?: ResourceCreatedOut; submitted?: boolean; message?: string; payloadKey: string;
}

export default function BulkImportPage() {
  const user = useAuthStore(s => s.user);
  const [rows, setRows] = useState<Row[]>([]);
  const [busy, setBusy] = useState(false);
  const running = useRef(false);
  const inputClass = 'min-h-11 w-full border border-control-border bg-surface px-3 py-2 text-sm';
  if (!isAdmin(user)) return <Notice tone="warning" title="批量上传仅对管理员开放">请使用管理员账号。</Notice>;
  function change(id: string, values: Partial<Row>) { setRows(current => current.map(r => r.id === id ? { ...r, ...values } : r)); }
  async function selectFiles(files: File[]) {
    if (running.current) return;
    if (!files.length) return;
    if (rows.length + files.length > 50) { toast.error('每批最多 50 个文件'); return; }
    if (files.some(f => !mimeByExtension[f.name.split('.').pop()?.toLowerCase() || ''] || f.size <= 0 || f.size > 50 * 1024 * 1024 || f.name.length > 300)) {
      toast.error('仅支持 PDF、PNG/JPEG、DOCX/PPTX，单文件需在 0–50 MiB 内，文件名不超过 300 字符'); return;
    }
    running.current = true; setBusy(true);
    try {
      const suggestions = await classifyFiles(files.map(f => f.name)).catch(error => {
        toast.error(`自动分类失败，可手动选择：${describeError(error).message}`);
        return files.map(file => ({ name: file.name, course_id: null, resource_type: 'other' as const, reason: '自动分类暂不可用，请手动选择', needs_confirmation: true, candidates: [], type_matches: [] }));
      });
      setRows(current => [...current, ...files.map((file, i): Row => ({
        id: crypto.randomUUID(), file, title: file.name.replace(/\.[^.]+$/, ''),
        course: suggestions[i].course_id || '', type: suggestions[i].resource_type,
        reason: `${suggestions[i].reason}${suggestions[i].needs_confirmation ? ' · 待人工核对' : ''}${suggestions[i].candidates.length > 1 ? ` · ${suggestions[i].candidates.map(c => `${c.name}(${c.code || '无代码'})`).join('、')}` : ''}`,
        confirmed: false, rights: 'unverified', source: '', author: '', license: '', payloadKey: crypto.randomUUID(),
      }))]);
    } catch (error) { toast.error(describeError(error).message); } finally { running.current = false; setBusy(false); }
  }
  async function run(submit: boolean) {
    if (running.current) return;
    running.current = true; setBusy(true);
    try {
      for (const original of rows) {
        if (original.submitted || !original.confirmed || !original.course || !original.title.trim()) continue;
        const row = { ...original };
        try {
          if (!row.resource && row.rights !== 'original' && !row.source.trim()) throw new Error('非原创资料请先填写来源说明');
          if (!row.resource) {
            row.message = '正在上传…'; change(row.id, row);
            row.upload ??= await createUpload({ original_name: row.file.name, declared_mime: mimeByExtension[row.file.name.split('.').pop()!.toLowerCase()], expected_size: row.file.size }, `${row.id}-upload`);
            change(row.id, row);
            if (!row.transferred) {
              await putFileToStorage(row.upload.upload_url, row.file, row.upload.content_type);
              row.transferred = true; change(row.id, row);
            }
            row.status = await completeUpload(row.upload.id, `${row.id}-complete`);
            change(row.id, row);
            if (!row.status.file_id) throw new Error('文件尚未完成登记，请稍后重试');
            row.resource = await createImportDraft({
              title: row.title.trim(), course_id: row.course, resource_type: row.type,
              files: [{ file_id: row.status.file_id, purpose: 'primary' }],
              rights: { rights_basis: row.rights, source: row.source || null, original_author: row.author || null, license: row.license || null, declaration_text_version: 'v1' },
            }, row.payloadKey);
            change(row.id, row);
          }
          if (submit) {
            row.status = await getUpload(row.upload!.id);
            if (row.status.scan_status !== 'passed') {
              row.message = `草稿已保存 · 安全检查：${row.status.scan_status || '等待中'}，通过后可再次提交`;
            } else {
              await submitVersion(row.resource.resource_id, row.resource.version_id, `${row.id}-submit`);
              row.submitted = true; row.message = '已提交审核';
            }
          } else { row.message = '草稿已保存'; }
        } catch (error) { row.message = describeError(error).message; }
        change(row.id, row);
      }
    } finally { running.current = false; setBusy(false); }
  }
  const eligible = rows.some(r => r.confirmed && r.course && r.title.trim() && !r.submitted);
  return <div className="space-y-5">
    <h2 className="text-2xl font-semibold">批量上传资料</h2>
    <p className="max-w-3xl text-sm text-text-secondary">按文件名自动匹配分类，请逐项核对分类与来源。</p>
    <fieldset disabled={busy} className="min-w-0 space-y-5">
      <label className="block border border-dashed border-control-border p-5 text-sm">选择文件（每批最多 50 个，单文件最多 50 MiB）<input aria-label="批量选择文件" type="file" multiple accept=".pdf,.png,.jpg,.jpeg,.docx,.pptx" className="mt-3 block w-full" onChange={e => { void selectFiles(Array.from(e.target.files || [])); e.target.value = ''; }} /></label>
      {rows.map((r, index) => <section key={r.id} className="min-w-0 space-y-3 border border-divider bg-surface p-4">
        <div className="flex items-start justify-between gap-3"><h3 className="min-w-0 break-all font-semibold">{index + 1}. {r.file.name}</h3><Button variant="ghost" disabled={!!r.upload} onClick={() => setRows(current => current.filter(item => item.id !== r.id))}>移除</Button></div>
        <p className="break-all text-xs text-text-secondary">匹配依据：{r.reason}</p>
        <fieldset disabled={!!r.resource} className="grid min-w-0 gap-3 md:grid-cols-2">
          <label className="block text-sm">标题<input value={r.title} maxLength={300} onChange={e => change(r.id, { title: e.target.value })} className={inputClass} /></label>
          <label className="block text-sm">资料类型<select value={r.type} onChange={e => change(r.id, { type: e.target.value as ResourceType })} className={inputClass}>{RESOURCE_TYPE_ORDER.map(type => <option key={type} value={type}>{RESOURCE_TYPE_LABELS[type]}</option>)}</select></label>
          <CoursePicker value={r.course} onChange={course => change(r.id, { course })} />
          <label className="block text-sm">权利依据<select value={r.rights} onChange={e => change(r.id, { rights: e.target.value as RightsBasis })} className={inputClass}><option value="unverified">来源待核实</option><option value="original">本人原创</option><option value="authorized">已获得授权</option><option value="open_license">开放许可</option><option value="third_party">第三方来源</option></select></label>
          <label className="block text-sm">来源说明<input maxLength={2000} value={r.source} onChange={e => change(r.id, { source: e.target.value })} className={inputClass} /></label>
          <label className="block text-sm">原作者<input maxLength={300} value={r.author} onChange={e => change(r.id, { author: e.target.value })} className={inputClass} /></label>
          <label className="block text-sm">许可说明<input maxLength={300} value={r.license} onChange={e => change(r.id, { license: e.target.value })} className={inputClass} /></label>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={r.confirmed} onChange={e => change(r.id, { confirmed: e.target.checked })} />已核对分类与来源，确认本项上传声明</label>
        </fieldset>
        {r.message && <p role="status" className="break-words text-sm text-text-secondary">{r.message}</p>}
        {r.resource && <Link className="inline-block text-sm underline" href={`/resources/${r.resource.resource_id}/versions/${r.resource.version_id}`}>查看与管理草稿</Link>}
      </section>)}
      <div className="flex flex-wrap gap-3"><Button variant="secondary" disabled={!eligible} onClick={() => void run(false)}>批量保存草稿 / 重试</Button><Button disabled={!eligible} onClick={() => void run(true)}>批量提交审核 / 刷新安全检查</Button></div>
    </fieldset>
    {busy && <p role="status" className="text-sm">正在处理，请保留此页面；失败项可重试，已保存草稿可在“我的资料”继续。</p>}
  </div>;
}
