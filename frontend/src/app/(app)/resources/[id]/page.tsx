import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';

import { Notice } from '@/components/common/Notice';
import { ResourceActions } from '@/components/resource/ResourceActions';
import type { CourseDetail } from '@/lib/api/catalog';
import type { ResourceDetailOut, VersionFilesOut } from '@/lib/api/content';
import { serverFetch } from '@/lib/api/server';
import { RESOURCE_TYPE_LABELS } from '@/lib/api/types';
import { formatDateTime, isUuid } from '@/lib/utils';

export const dynamic = 'force-dynamic';

const freshnessLabels: Record<string, string> = {
  current: '现行内容',
  possibly_outdated: '可能过时',
  outdated: '已过时',
};

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<Metadata> {
  const { id } = await params;
  if (!isUuid(id)) return { title: '资料' };

  const { data } = await serverFetch<ResourceDetailOut>(`/resources/${id}`);
  const title = data?.published_version?.title;
  if (!data || !title) {
    // 未发布或已下架的私有资源不进入索引（§19.3）。
    return { title: '资料', robots: { index: false, follow: false } };
  }
  return {
    title,
    description: data.published_version?.description?.slice(0, 140) ?? undefined,
    alternates: { canonical: `/resources/${data.id}` },
  };
}

export default async function ResourceDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  if (!isUuid(id)) notFound();

  const resourceResult = await serverFetch<ResourceDetailOut>(`/resources/${id}`);
  if (resourceResult.error?.status === 404) notFound();

  const resource = resourceResult.data;
  if (!resource) {
    return (
      <Notice tone="warning" title="资料信息暂时无法加载">
        {resourceResult.error?.message ?? '请稍后重试。'}
      </Notice>
    );
  }

  const version = resource.published_version;
  const [courseResult, filesResult] = await Promise.all([
    serverFetch<CourseDetail>(`/courses/${resource.course_id}`),
    version
      ? serverFetch<VersionFilesOut>(`/resources/${id}/versions/${version.id}/files`)
      : Promise.resolve({ data: null, error: null }),
  ]);

  const course = courseResult.data;
  const files = filesResult.data?.items ?? [];

  return (
    <div className="space-y-8">
      <header className="bg-violet px-6 py-8 text-on-color">
        <p className="dn-kicker">
          {RESOURCE_TYPE_LABELS[resource.resource_type]} · V{version?.version_no ?? '—'}
        </p>
        <h1 className="dn-display mt-2 text-3xl md:text-4xl">
          {version?.title ?? '（该资料暂无公开版本）'}
        </h1>
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-on-color">
          <span>
            {course ? (
              <Link href={`/courses/${course.id}`} className="underline underline-offset-2">
                {course.name}
                {course.code ? `（${course.code}）` : ''}
              </Link>
            ) : (
              '课程归属待确认'
            )}
          </span>
          <span>{freshnessLabels[resource.freshness_status] ?? resource.freshness_status}</span>
          <span>更新于 {formatDateTime(resource.updated_at)}</span>
        </div>
      </header>

      {version?.description && (
        <section aria-labelledby="resource-desc">
          <h2 id="resource-desc" className="dn-display text-2xl">
            内容说明
          </h2>
          <p className="mt-3 max-w-3xl whitespace-pre-line text-text-secondary">
            {version.description}
          </p>
        </section>
      )}

      {version?.change_summary && (
        <section aria-labelledby="resource-change">
          <h2 id="resource-change" className="dn-display text-2xl">
            本版变更
          </h2>
          <p className="mt-3 max-w-3xl whitespace-pre-line text-text-secondary">
            {version.change_summary}
          </p>
        </section>
      )}

      <section aria-labelledby="resource-files">
        <h2 id="resource-files" className="dn-display text-2xl">
          文件与预览
        </h2>
        <div className="mt-3">
          {filesResult.error ? (
            <Notice tone="warning" title="文件清单暂时无法加载">
              {filesResult.error.message}
            </Notice>
          ) : (
            <ResourceActions
              resourceId={resource.id}
              versionId={version?.id ?? null}
              versionNo={version?.version_no ?? null}
              publicationStatus={resource.publication_status}
              previewAvailable={resource.preview_available}
              files={files}
            />
          )}
        </div>
      </section>

      <section aria-labelledby="resource-known-issues">
        <h2 id="resource-known-issues" className="dn-display text-2xl">
          已知问题与反馈
        </h2>
        <p className="mt-3 max-w-3xl text-sm text-text-secondary">
          如果内容有误、包含不应公开的信息，或你不清楚来源是否允许分发，请通过资料页的「举报/纠错」提交。
          举报会进入独立工单，受理不等于认定违规。
        </p>
      </section>
    </div>
  );
}
