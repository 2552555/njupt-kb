import type { Metadata } from 'next';
import Link from 'next/link';

import { CourseCard } from '@/components/catalog/CourseCard';
import { Notice } from '@/components/common/Notice';
import type { CollegeBrief, CourseListItem, MajorBrief } from '@/lib/api/catalog';
import { serverFetch } from '@/lib/api/server';
import type { CursorPage } from '@/lib/api/types';
import { isUuid } from '@/lib/utils';

export const metadata: Metadata = {
  title: '课程',
  description: '按课程名称、代码与专业浏览南京邮电大学课程，进入课程查看开课范围与分类资料。',
  alternates: { canonical: '/courses' },
};

export const dynamic = 'force-dynamic';

interface CoursesSearchParams {
  q?: string;
  college_id?: string;
  major_id?: string;
  cursor?: string;
}

function buildQuery(params: Record<string, string | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) search.set(key, value);
  }
  const query = search.toString();
  return query ? `?${query}` : '';
}

export default async function CoursesPage({
  searchParams,
}: {
  searchParams: Promise<CoursesSearchParams>;
}) {
  const params = await searchParams;
  const keyword = params.q?.trim() || '';
  const collegeId = isUuid(params.college_id) ? params.college_id : '';
  const majorId = isUuid(params.major_id) ? params.major_id : '';
  const cursor = params.cursor || '';

  const query = buildQuery({
    q: keyword || undefined,
    college_id: collegeId || undefined,
    major_id: majorId || undefined,
    cursor: cursor || undefined,
    limit: '20',
  });

  const [listResult, collegesResult, majorsResult] = await Promise.all([
    serverFetch<CursorPage<CourseListItem>>(`/courses${query}`),
    serverFetch<CollegeBrief[]>('/colleges'),
    serverFetch<MajorBrief[]>('/majors'),
  ]);

  const colleges = collegesResult.data ?? [];
  const majors = majorsResult.data ?? [];
  const hasFilters = Boolean(keyword || collegeId || majorId);

  const nextCursor = listResult.data?.next_cursor ?? null;

  return (
    <div className="space-y-6">
      <header className="bg-ink px-6 py-8 text-on-ink">
        <p className="dn-kicker">COURSES</p>
        <h1 className="dn-display mt-2 text-3xl">课程列表</h1>
        <p className="mt-2 max-w-2xl text-on-ink">
          按课程名称、代码、简称或专业筛选。课程代码可能随培养方案变化，同名课程默认分开。
        </p>
      </header>

      {/* 原生 GET 表单：不依赖 JS 即可完成筛选与提交（§39 A20 键盘可达）。 */}
      <form method="get" className="grid gap-3 border border-divider bg-surface p-4 md:grid-cols-[1fr_auto_auto_auto]">
        <div>
          <label htmlFor="course-q" className="mb-1 block text-sm font-medium text-text-primary">
            课程名 / 代码 / 简称
          </label>
          <input
            id="course-q"
            name="q"
            type="search"
            defaultValue={keyword}
            placeholder="例如：数据结构、B0300010、数构"
            className="min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary placeholder:text-text-secondary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
          />
        </div>
        <div>
          <label htmlFor="course-college" className="mb-1 block text-sm font-medium text-text-primary">
            学院
          </label>
          <select
            id="course-college"
            name="college_id"
            defaultValue={collegeId}
            className="min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
          >
            <option value="">全部学院</option>
            {colleges.map((college) => (
              <option key={college.id} value={college.id}>
                {college.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="course-major" className="mb-1 block text-sm font-medium text-text-primary">
            专业
          </label>
          <select
            id="course-major"
            name="major_id"
            defaultValue={majorId}
            className="min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
          >
            <option value="">全部专业</option>
            {majors.map((major) => (
              <option key={major.id} value={major.id}>
                {major.college_name} · {major.name}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-end gap-2">
          <button
            type="submit"
            className="inline-flex min-h-11 items-center bg-brand px-4 text-sm font-medium text-on-color hover:bg-brand/90"
          >
            筛选
          </button>
          {hasFilters && (
            <Link
              href="/courses"
              className="inline-flex min-h-11 items-center border border-control-border px-4 text-sm font-medium text-text-primary hover:bg-canvas"
            >
              清除
            </Link>
          )}
        </div>
      </form>

      {collegesResult.error && (
        <Notice tone="info" title="学院与专业筛选项暂时不可用">
          仍可按关键词搜索课程。{collegesResult.error.message}
        </Notice>
      )}

      {listResult.error ? (
        <Notice tone="warning" title="课程列表暂时无法加载">
          {listResult.error.message}。这不表示没有课程，请稍后重试。
        </Notice>
      ) : listResult.data && listResult.data.items.length > 0 ? (
        <>
          <p className="text-sm text-text-secondary">
            共显示 {listResult.data.items.length} 门课程
          </p>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {listResult.data.items.map((course) => (
              <CourseCard key={course.id} course={course} />
            ))}
          </div>
          {nextCursor && (
            <div className="flex justify-center pt-2">
              <Link
                href={`/courses${buildQuery({
                  q: keyword || undefined,
                  college_id: collegeId || undefined,
                  major_id: majorId || undefined,
                  cursor: nextCursor,
                })}`}
                className="inline-flex min-h-11 items-center border border-control-border px-5 text-sm font-medium text-text-primary hover:bg-canvas"
              >
                下一页
              </Link>
            </div>
          )}
        </>
      ) : (
        <Notice tone="info" title={hasFilters ? '没有匹配的课程' : '课程目录中还没有课程'}>
          {hasFilters
            ? '可以放宽筛选条件，或换一个课程名、代码或简称再试。'
            : '目录由维护者导入真实数据后在此显示。'}
        </Notice>
      )}
    </div>
  );
}
