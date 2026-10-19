import { RequestsClient } from '@/components/common/RequestsClient';
import { Notice } from '@/components/common/Notice';
import type { CourseDetail } from '@/lib/api/catalog';
import { serverFetch } from '@/lib/api/server';
import { firstSearchParam, isUuid } from '@/lib/utils';

export const dynamic = 'force-dynamic';

export default async function RequestsPage({ searchParams }: {
  searchParams: Promise<Partial<Record<'course_id' | 'new' | 'sort' | 'keyword', string | string[]>>>;
}) {
  const params = Object.fromEntries(Object.entries(await searchParams).map(([key, value]) => [key, firstSearchParam(value)]));
  const result = params.course_id && isUuid(params.course_id)
    ? await serverFetch<CourseDetail>(`/courses/${params.course_id}`) : null;
  // 「最热」入口：sort=hot 由 URL 带入，key 包含排序使切换时重挂载，避免旧分页混入。
  const initialSort = params.sort === 'hot' ? 'hot' : 'latest';
  return <>
    {params.course_id && !result?.data && <Notice tone="warning" title="未能带入课程">请在表单中重新选择课程。</Notice>}
    <RequestsClient
      key={`${params.course_id ?? ''}:${params.new ?? ''}:${initialSort}:${params.keyword ?? ''}`}
      initialCourse={result?.data ?? null}
      initialOpen={params.new === '1'}
      initialSort={initialSort}
      initialKeyword={params.keyword?.slice(0, 2000) ?? ''}
    />
  </>;
}
