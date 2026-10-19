import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';

import { MarkdownBody } from '@/components/content/MarkdownBody';
import { Notice } from '@/components/common/Notice';
import type { GuideArticle } from '@/lib/api/guides';
import { serverFetch } from '@/lib/api/server';
import { isUuid } from '@/lib/utils';

export const dynamic = 'force-dynamic';

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<Metadata> {
  const { id } = await params;
  if (!isUuid(id)) return { title: '课程攻略' };
  const result = await serverFetch<GuideArticle>(`/guides/${id}`);
  return { title: result.data?.title ?? '课程攻略' };
}

const STATUS_LABEL: Record<string, string> = {
  draft: '草稿（仅自己可见）',
  pending: '审核中',
  published: '已发布',
  rejected: '未通过审核',
  withdrawn: '已撤回',
};

export default async function GuideDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!isUuid(id)) notFound();
  const result = await serverFetch<GuideArticle>(`/guides/${id}`);
  if (result.error?.status === 404) notFound();
  if (!result.data) {
    return (
      <Notice tone="warning" title="攻略暂时无法加载">
        {result.error?.message ?? '请稍后重试。'}
      </Notice>
    );
  }
  const guide = result.data;
  const isPublished = guide.source === 'published';

  return (
    <article className="space-y-6">
      <header className="border-b border-divider pb-6">
        <Link
          href={`/courses/${guide.course_id}/guides`}
          className="text-sm text-brand underline underline-offset-2"
        >
          返回课程攻略
        </Link>
        <h1 className="dn-display mt-4 text-3xl">{guide.title}</h1>
        {guide.summary && <p className="mt-3 text-text-secondary">{guide.summary}</p>}
        <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-text-secondary">
          <span>{isPublished ? `第 ${guide.version_no} 版` : STATUS_LABEL[guide.status] ?? guide.status}</span>
          {guide.published_at && <span>发布于 {new Date(guide.published_at).toLocaleDateString('zh-CN')}</span>}
          <span>内容由用户投稿，请自行核对适用学期与教师</span>
        </div>
      </header>

      {!isPublished && (
        <Notice tone="warning" title="当前不是公开版本">
          你看到的是作者或审核人员可见的版本，其他访客只会看到已发布内容。
        </Notice>
      )}

      <MarkdownBody markdown={guide.body_markdown} />

      <footer className="border-t border-divider pt-6 text-sm text-text-secondary">
        <p>
          攻略是个人经验，不代表课程官方要求。发现错误可在资料页提交纠错，或联系管理员处理。
        </p>
      </footer>
    </article>
  );
}
