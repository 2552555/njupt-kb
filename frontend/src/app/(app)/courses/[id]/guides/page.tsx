import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';

import { Notice } from '@/components/common/Notice';
import { ResourceCard } from '@/components/resource/ResourceCard';
import type { CourseDetail } from '@/lib/api/catalog';
import type { ResourceListOut } from '@/lib/api/content';
import type { GuideListItem } from '@/lib/api/guides';
import { serverFetch } from '@/lib/api/server';
import { formatDate, isUuid } from '@/lib/utils';

export const metadata: Metadata = { title: '课程攻略' };
export const dynamic = 'force-dynamic';

export default async function CourseGuidesPage({ params, searchParams }: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ cursor?: string }>;
}) {
  const { id } = await params;
  if (!isUuid(id)) notFound();
  const { cursor } = await searchParams;
  const [courseResult, guidesResult, articlesResult] = await Promise.all([
    serverFetch<CourseDetail>(`/courses/${id}`),
    serverFetch<ResourceListOut>(`/resources?course_id=${id}&resource_type=guide&limit=12${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`),
    serverFetch<{ items: GuideListItem[]; has_more: boolean }>(`/guides?course_id=${id}&limit=12`),
  ]);
  if (courseResult.error?.status === 404) notFound();
  const course = courseResult.data;
  if (!course) return <Notice tone="warning" title="课程暂时无法加载">请稍后刷新重试。</Notice>;
  const guides = guidesResult.data;
  const articles = articlesResult.data;
  return (
    <div className="space-y-6">
      <header className="border-b border-divider pb-6">
        <Link href={`/courses/${id}`} className="text-sm text-brand underline underline-offset-2">返回 {course.name}</Link>
        <h1 className="dn-display mt-4 text-3xl">{course.name} · 课程攻略</h1>
        <p className="mt-3 text-text-secondary">学习路线、复习方法与课程经验。阅读前请核对适用学期、教师和资料时效。</p>
        <div className="mt-4 flex flex-wrap gap-2">
          <Link href="/me/guides" className="inline-flex min-h-11 items-center bg-brand px-4 text-sm font-medium text-on-color">在线写攻略</Link>
          <Link href={`/upload?course_id=${id}&type=guide`} className="inline-flex min-h-11 items-center border border-control-border px-4 text-sm">上传攻略文件</Link>
        </div>
      </header>

      <section aria-labelledby="guide-articles" className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="guide-articles" className="text-lg font-semibold">站内文章</h2>
          <p className="text-xs text-text-secondary">按发布顺序展示，内容经审核后公开</p>
        </div>
        {articlesResult.error ? (
          <Notice tone="warning" title="文章暂时无法加载">{articlesResult.error.message}。请稍后重试。</Notice>
        ) : articles && articles.items.length > 0 ? (
          <ul className="divide-y divide-divider border border-divider bg-surface">
            {articles.items.map((article) => (
              <li key={article.id} className="p-4">
                <Link href={`/guides/${article.id}`} className="text-base font-medium underline underline-offset-2">
                  {article.title}
                </Link>
                {article.summary && <p className="mt-1 text-sm text-text-secondary">{article.summary}</p>}
                <p className="mt-1 text-xs text-text-secondary">
                  {article.published_at ? `发布于 ${formatDate(article.published_at)}` : '已发布'}
                  {article.published_version_no ? ` · 第 ${article.published_version_no} 版` : ''}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <Notice tone="info" title="还没有站内文章">可以点「在线写攻略」，提交审核通过后会展示在这里。</Notice>
        )}
      </section>

      <section aria-labelledby="guide-files" className="space-y-3">
        <h2 id="guide-files" className="text-lg font-semibold">攻略文件</h2>
        {guidesResult.error ? (
          <Notice tone="warning" title="攻略暂时无法加载">{guidesResult.error.message}。请稍后重试。</Notice>
        ) : guides && guides.items.length > 0 ? (
          <div className="grid gap-4 sm:grid-cols-2">
            {guides.items.map((item) => (
              <ResourceCard key={item.id} resourceId={item.id} title={item.title ?? '（未命名攻略）'}
                resourceType={item.resource_type} courseName={course.name} courseCode={course.code} freshness={item.freshness_status} />
            ))}
          </div>
        ) : (
          <Notice tone="info" title={cursor ? '这一页没有攻略文件' : '这门课还没有已发布的攻略文件'}>可以上传 PDF/Word，或直接在线写攻略。</Notice>
        )}
        <nav aria-label="攻略文件分页" className="flex gap-6 text-sm text-brand">
          {cursor && <Link href={`/courses/${id}/guides`} className="min-h-11 content-center underline">返回第一页</Link>}
          {guides?.has_more && guides.next_cursor && <Link href={`/courses/${id}/guides?cursor=${encodeURIComponent(guides.next_cursor)}`} className="min-h-11 content-center underline">下一页攻略</Link>}
        </nav>
      </section>
    </div>
  );
}
