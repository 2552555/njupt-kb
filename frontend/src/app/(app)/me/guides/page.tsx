'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';

import { Notice } from '@/components/common/Notice';
import { MarkdownBody } from '@/components/content/MarkdownBody';
import { CoursePicker } from '@/components/catalog/CoursePicker';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { SkeletonList } from '@/components/ui/Skeleton';
import { describeError } from '@/lib/api/client';
import {
  createGuide,
  getGuide,
  listMyGuides,
  submitGuide,
  updateGuide,
  withdrawGuide,
  type GuideListItem,
} from '@/lib/api/guides';
import { formatDateTime } from '@/lib/utils';

const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  pending: '审核中',
  published: '已发布',
  rejected: '未通过',
  withdrawn: '已撤回',
};

type EditorState = {
  id?: string;
  revision?: number;
  courseId: string;
  title: string;
  summary: string;
  body: string;
};

const EMPTY_EDITOR: EditorState = { courseId: '', title: '', summary: '', body: '' };

export default function MyGuidesPage() {
  const queryClient = useQueryClient();
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [preview, setPreview] = useState(false);

  const query = useQuery({ queryKey: ['my-guides'], queryFn: listMyGuides });

  function invalidate() {
    queryClient.invalidateQueries({ queryKey: ['my-guides'] });
  }

  const saveMutation = useMutation({
    mutationFn: async ({ submit }: { submit: boolean }) => {
      if (!editor) throw new Error('没有正在编辑的攻略');
      let articleId = editor.id;
      if (!articleId) {
        const created = await createGuide({
          course_id: editor.courseId,
          title: editor.title,
          summary: editor.summary || undefined,
          body_markdown: editor.body,
        });
        articleId = created.id;
      } else {
        await updateGuide(articleId, {
          expected_revision: editor.revision ?? 1,
          title: editor.title,
          summary: editor.summary,
          body_markdown: editor.body,
        });
      }
      if (submit) await submitGuide(articleId);
      return { articleId, submit };
    },
    onSuccess: ({ submit }) => {
      toast.success(submit ? '已提交审核' : '草稿已保存');
      setEditor(null);
      setPreview(false);
      invalidate();
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const submitMutation = useMutation({
    mutationFn: (item: GuideListItem) => submitGuide(item.id),
    onSuccess: () => {
      toast.success('已提交审核');
      invalidate();
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const withdrawMutation = useMutation({
    mutationFn: (item: GuideListItem) => {
      const reason = window.prompt('撤回理由（至少 2 个字符）', '内容需要重写');
      if (!reason || reason.trim().length < 2) throw new Error('需要填写撤回理由');
      return withdrawGuide(item.id, reason.trim());
    },
    onSuccess: () => {
      toast.success('已撤回');
      invalidate();
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  async function openEditor(item: GuideListItem) {
    try {
      const article = await getGuide(item.id);
      setEditor({
        id: article.id,
        revision: article.revision,
        courseId: article.course_id,
        title: article.title,
        summary: article.summary ?? '',
        body: article.body_markdown,
      });
      setPreview(false);
    } catch (error) {
      toast.error(describeError(error).message);
    }
  }

  const items = query.data?.items ?? [];

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4 border-b border-divider pb-6">
        <div>
          <h1 className="dn-display text-3xl">我的课程攻略</h1>
          <p className="mt-3 text-text-secondary">
            攻略是可在站内编辑的文章：保存草稿、提交审核、按版本发布。发布后编辑不会覆盖已公开正文。
          </p>
        </div>
        <Button onClick={() => { setEditor({ ...EMPTY_EDITOR }); setPreview(false); }}>
          写一篇攻略
        </Button>
      </header>

      {editor && (
        <section className="border border-divider bg-surface p-4 sm:p-6" aria-label="攻略编辑器">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-semibold">{editor.id ? '编辑攻略' : '新建攻略'}</h2>
            <div className="flex gap-2">
              <Button variant="ghost" size="sm" onClick={() => setPreview((value) => !value)}>
                {preview ? '返回编辑' : '预览正文'}
              </Button>
              <Button variant="ghost" size="sm" onClick={() => { setEditor(null); setPreview(false); }}>
                关闭
              </Button>
            </div>
          </div>

          {preview ? (
            <div className="mt-4 border-t border-divider pt-4">
              <MarkdownBody markdown={editor.body || '_正文还是空的。_'} />
            </div>
          ) : (
            <div className="mt-4 space-y-4">
              {!editor.id && (
                <CoursePicker
                  value={editor.courseId}
                  onChange={(courseId) => setEditor((prev) => (prev ? { ...prev, courseId } : prev))}
                />
              )}
              <div>
                <label htmlFor="guide-title" className="block text-sm font-medium">标题</label>
                <input
                  id="guide-title"
                  value={editor.title}
                  maxLength={200}
                  onChange={(event) => setEditor((prev) => (prev ? { ...prev, title: event.target.value } : prev))}
                  className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3 text-sm"
                  placeholder="例如：数据结构期末复习路线"
                />
              </div>
              <div>
                <label htmlFor="guide-summary" className="block text-sm font-medium">摘要（可选）</label>
                <input
                  id="guide-summary"
                  value={editor.summary}
                  maxLength={500}
                  onChange={(event) => setEditor((prev) => (prev ? { ...prev, summary: event.target.value } : prev))}
                  className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3 text-sm"
                  placeholder="一句话说明适用范围与重点"
                />
              </div>
              <div>
                <label htmlFor="guide-body" className="block text-sm font-medium">
                  正文（Markdown，至少 50 个字符）
                </label>
                <textarea
                  id="guide-body"
                  value={editor.body}
                  rows={16}
                  maxLength={100000}
                  onChange={(event) => setEditor((prev) => (prev ? { ...prev, body: event.target.value } : prev))}
                  className="mt-1 w-full border border-control-border bg-surface px-3 py-2 font-mono text-sm"
                  placeholder={'## 复习顺序\n\n1. 先过作业题\n2. 再对照往年试卷'}
                />
                <p className="mt-1 text-xs text-text-secondary">{editor.body.length} 个字符</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  onClick={() => saveMutation.mutate({ submit: false })}
                  isLoading={saveMutation.isPending}
                  disabled={!editor.courseId || !editor.title.trim() || editor.body.trim().length < 50}
                >
                  保存草稿
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => saveMutation.mutate({ submit: true })}
                  isLoading={saveMutation.isPending}
                  disabled={!editor.courseId || !editor.title.trim() || editor.body.trim().length < 50}
                >
                  保存并提交审核
                </Button>
              </div>
              <p className="text-xs text-text-secondary">
                正文按 Markdown 渲染；原始 HTML 不会被执行，外链会标注为站外地址。
              </p>
            </div>
          )}
        </section>
      )}

      {query.isLoading ? (
        <SkeletonList count={4} />
      ) : query.isError ? (
        <Notice tone="error" title="无法加载攻略">{describeError(query.error).message}</Notice>
      ) : items.length === 0 ? (
        <EmptyState title="还没有攻略" description="可以从一门课的复习经验开始，保存草稿后再提交审核。" />
      ) : (
        <ul className="divide-y divide-divider border border-divider bg-surface">
          {items.map((item) => (
            <li key={item.id} className="flex flex-wrap items-start justify-between gap-3 p-4">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-base font-medium">{item.title}</h2>
                  <span className="border border-divider px-2 py-0.5 text-xs text-text-secondary">
                    {STATUS_LABEL[item.status] ?? item.status}
                  </span>
                  {item.published_version_no && (
                    <span className="text-xs text-text-secondary">第 {item.published_version_no} 版已公开</span>
                  )}
                </div>
                {item.summary && <p className="mt-1 text-sm text-text-secondary">{item.summary}</p>}
                <p className="mt-1 text-xs text-text-secondary">更新于 {formatDateTime(item.updated_at)}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                {item.status === 'published' && (
                  <Link
                    href={`/guides/${item.id}`}
                    className="inline-flex min-h-11 items-center border border-control-border px-3 text-sm"
                  >
                    查看
                  </Link>
                )}
                {item.status !== 'pending' && item.status !== 'withdrawn' && (
                  <Button variant="secondary" size="sm" onClick={() => openEditor(item)}>
                    编辑
                  </Button>
                )}
                {(item.status === 'draft' || item.status === 'rejected') && (
                  <Button
                    size="sm"
                    onClick={() => submitMutation.mutate(item)}
                    isLoading={submitMutation.isPending}
                  >
                    提交审核
                  </Button>
                )}
                {item.status !== 'withdrawn' && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => withdrawMutation.mutate(item)}
                    isLoading={withdrawMutation.isPending}
                  >
                    撤回
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      <Notice tone="info" title="审核与公开规则">
        攻略提交后进入审核队列；通过后才会出现在课程攻略页。发布后的修改会生成新版本，审核通过前读者仍看到上一版。
      </Notice>
    </div>
  );
}
