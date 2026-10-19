import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';

import { Notice } from '@/components/common/Notice';
import { ResourceCard } from '@/components/resource/ResourceCard';
import { Recommendations } from '@/components/resource/Recommendations';
import { CourseFollowButton } from '@/components/catalog/CourseFollowButton';
import type { CourseDetail, OfferingItem } from '@/lib/api/catalog';
import type { ResourceListItem } from '@/lib/api/content';
import { serverFetch } from '@/lib/api/server';
import {
  isResourceType,
  RESOURCE_TYPE_LABELS,
  RESOURCE_TYPE_ORDER,
  type CursorPage,
  type ResourceType,
} from '@/lib/api/types';
import { firstSearchParam, isUuid } from '@/lib/utils';

export const dynamic = 'force-dynamic';

const courseStatusLabels: Record<string, string> = {
  active: '正常开设',
  deprecated: '已停开',
  merged: '已合并',
  pending_classification: '待归类',
};

const seasonLabels: Record<string, string> = {
  spring: '春季学期',
  fall: '秋季学期',
  summer: '夏季学期',
};

async function loadCourse(id: string) {
  return serverFetch<CourseDetail>(`/courses/${id}`);
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<Metadata> {
  const { id } = await params;
  if (!isUuid(id)) return { title: '课程' };

  const { data } = await loadCourse(id);
  if (!data) return { title: '课程' };

  const description = data.description
    ? data.description.slice(0, 140)
    : `${data.name} 的课程资料、开课范围与适用培养方案。`;

  return {
    title: data.code ? `${data.name}（${data.code}）` : data.name,
    description,
    alternates: { canonical: `/courses/${data.id}` },
  };
}

export default async function CourseDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Partial<Record<'type' | 'cursor' | 'sort', string | string[]>>>;
}) {
  const { id } = await params;
  if (!isUuid(id)) notFound();

  const rawParams = await searchParams;
  const type = firstSearchParam(rawParams.type);
  const cursor = firstSearchParam(rawParams.cursor);
  const rawSort = firstSearchParam(rawParams.sort);
  const sort = rawSort === 'oldest' || rawSort === 'hot' ? rawSort : 'latest';
  const typeFilter: ResourceType | undefined =
    type && isResourceType(type) ? type : undefined;
  function resourceLink(nextType: ResourceType | null = typeFilter ?? null, nextSort = sort, nextCursor?: string) {
    const query = new URLSearchParams();
    if (nextType) query.set('type', nextType);
    if (nextSort !== 'latest') query.set('sort', nextSort);
    if (nextCursor) query.set('cursor', nextCursor);
    return `/courses/${id}${query.size ? `?${query}` : ''}`;
  }

  const courseResult = await loadCourse(id);
  if (courseResult.error?.status === 404) notFound();
  const course = courseResult.data;

  const [offeringsResult, resourcesResult] = await Promise.all([
    serverFetch<OfferingItem[]>(`/courses/${id}/offerings`),
    serverFetch<CursorPage<ResourceListItem>>(
      `/resources?course_id=${id}${typeFilter ? `&resource_type=${typeFilter}` : ''}&sort=${sort}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}&limit=20`,
    ),
  ]);

  if (!course) {
    return (
      <Notice tone="warning" title="课程信息暂时无法加载">
        {courseResult.error?.message ?? '请稍后重试。'}
      </Notice>
    );
  }

  // 分类计数只列有可见资源的类型，顺序与类型筛选项一致。
  const countedTypes = RESOURCE_TYPE_ORDER.filter(
    (resourceType) => (course.resource_counts?.[resourceType] ?? 0) > 0,
  );

  return (
    <div className="space-y-8">
      <header className="bg-cyan px-6 py-8 text-on-color">
        <p className="dn-kicker">COURSE</p>
        <h1 className="dn-display mt-2 text-3xl md:text-4xl">{course.name}</h1>
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-on-color">
          {course.code && <span>课程代码 {course.code}</span>}
          <span>{course.college_name ?? '归属学院待确认'}</span>
          <span>{courseStatusLabels[course.status] ?? course.status}</span>
        </div>
        {course.aliases.length > 0 && (
          <p className="mt-2 text-sm text-on-color">别名：{course.aliases.join('、')}</p>
        )}
      </header>

      {/* 关注与求资料：P2 共建入口，与收藏分开（§20.2）。 */}
      <div className="flex flex-wrap items-center gap-3">
        <Link href={`/upload?course_id=${course.id}`} className="inline-flex min-h-11 items-center bg-brand px-4 text-sm font-medium text-on-color hover:bg-brand/90">上传这门课的资料</Link>
        <CourseFollowButton courseId={course.id} />
        <Link
          href={`/requests?course_id=${course.id}&new=1`}
          className="inline-flex min-h-11 items-center border border-control-border px-4 text-sm font-medium text-text-primary hover:bg-canvas"
        >
          为这门课求资料
        </Link>
      </div>

      <section aria-label="课程攻略" className="border-y border-divider py-5">
        <h2 className="dn-display text-2xl">课程攻略</h2>
        <p className="mt-2 text-sm text-text-secondary">了解学习路线、复习方法和课程经验，注意核对适用学期与教师。</p>
        <div className="mt-3 flex flex-wrap gap-4 text-sm">
          <Link href={`/courses/${id}/guides`} className="min-h-11 content-center text-brand underline underline-offset-2">查看课程攻略</Link>
          <Link href={`/upload?course_id=${id}&type=guide`} className="min-h-11 content-center text-brand underline underline-offset-2">分享课程攻略</Link>
        </div>
      </section>

      <section aria-labelledby="course-resources">
        <h2 id="course-resources" className="dn-display text-2xl">
          分类资料
        </h2>
        {/* 分类计数（beta14）：与列表口径一致，只统计当前用户可见的已发布资源。 */}
        {countedTypes.length > 0 && (
          <p className="mt-2 text-sm text-text-secondary">
            {countedTypes
              .map((resourceType) => `${RESOURCE_TYPE_LABELS[resourceType]} ${course.resource_counts?.[resourceType] ?? 0}`)
              .join(' · ')}
          </p>
        )}
        <div className="mt-3 flex flex-wrap gap-2">
          <Link
            href={resourceLink(null, sort)}
            className={`inline-flex min-h-11 items-center px-3 text-sm font-medium ${
              !typeFilter
                ? 'bg-brand text-on-color'
                : 'border border-control-border text-text-primary hover:bg-canvas'
            }`}
          >
            全部类型
          </Link>
          {RESOURCE_TYPE_ORDER.map((resourceType) => (
            <Link
              key={resourceType}
              href={resourceLink(resourceType)}
              className={`inline-flex min-h-11 items-center px-3 text-sm font-medium ${
                typeFilter === resourceType
                  ? 'bg-brand text-on-color'
                  : 'border border-control-border text-text-primary hover:bg-canvas'
              }`}
            >
              {RESOURCE_TYPE_LABELS[resourceType]}
            </Link>
          ))}
        </div>

        <nav aria-label="课程资料排序" className="mt-3 flex flex-wrap gap-4 text-sm">
          {([['latest', '最新'], ['oldest', '最早'], ['hot', '热门']] as const).map(([value, label]) => (
            <Link key={value} href={resourceLink(typeFilter ?? null, value)} aria-current={sort === value ? 'true' : undefined} className={`min-h-11 content-center underline ${sort === value ? 'font-semibold text-brand' : 'text-text-secondary'}`}>{label}</Link>
          ))}
        </nav>

        <div className="mt-4">
          {resourcesResult.error ? (
            <Notice tone="warning" title="资料列表暂时无法加载">
              {resourcesResult.error.message}
            </Notice>
          ) : resourcesResult.data && resourcesResult.data.items.length > 0 ? (
            <div className="dn-stagger grid gap-4 sm:grid-cols-2">
              {resourcesResult.data.items.map((item) => (
                <ResourceCard
                  key={item.id}
                  resourceId={item.id}
                  title={item.title ?? '（未命名资料）'}
                  resourceType={item.resource_type}
                  courseName={course.name}
                  courseCode={course.code}
                  freshness={item.freshness_status}
                />
              ))}
            </div>
          ) : (
            <Notice tone="info" title={typeFilter ? '该类型下还没有资料' : '该课程还没有已发布的资料'}>
              {typeFilter
                ? '切换到其他类型看看。'
                : '登录后可为这门课上传资料。'}
            </Notice>
          )}
        </div>
        <div className="mt-4 flex flex-wrap gap-4 text-sm">
          {cursor && <Link href={resourceLink()} className="min-h-11 content-center text-brand underline">回到第一页</Link>}
          {resourcesResult.data?.has_more && resourcesResult.data.next_cursor && <Link href={resourceLink(typeFilter ?? null, sort, resourcesResult.data.next_cursor)} className="min-h-11 content-center text-brand underline">下一页资料</Link>}
        </div>
      </section>

      <Recommendations courseId={id} />

      <details className="border border-divider bg-surface p-4">
        <summary className="cursor-pointer font-medium text-text-primary">课程介绍、培养方案与开课范围</summary>
        <div className="mt-6 space-y-6">
          {course.description && (
            <section aria-labelledby="course-intro">
              <h2 id="course-intro" className="dn-display text-2xl">
                课程介绍
              </h2>
              <p className="mt-3 max-w-3xl whitespace-pre-line text-text-secondary">
                {course.description}
              </p>
            </section>
          )}

          <section aria-labelledby="course-curricula">
            <h2 id="course-curricula" className="dn-display text-2xl">
              适用培养方案
            </h2>
            {course.curricula.length > 0 ? (
              <div className="mt-3 overflow-x-auto border border-divider">
                <table className="w-full min-w-[640px] border-collapse text-sm">
                  <thead>
                    <tr className="bg-canvas text-left text-text-secondary">
                      <th className="px-4 py-3 font-medium">专业</th>
                      <th className="px-4 py-3 font-medium">培养方案</th>
                      <th className="px-4 py-3 font-medium">入学年级</th>
                      <th className="px-4 py-3 font-medium">课程性质</th>
                      <th className="px-4 py-3 font-medium">建议学期</th>
                    </tr>
                  </thead>
                  <tbody>
                    {course.curricula.map((link) => (
                      <tr key={link.curriculum_id} className="border-t border-divider">
                        <td className="px-4 py-3 text-text-primary">{link.major_name}</td>
                        <td className="px-4 py-3 text-text-secondary">{link.curriculum_name}</td>
                        <td className="px-4 py-3 text-text-secondary">
                          {link.entry_year ?? '未标注'}
                        </td>
                        <td className="px-4 py-3 text-text-secondary">{link.requirement_type}</td>
                        <td className="px-4 py-3 text-text-secondary">
                          {link.recommended_term ?? '未标注'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Notice tone="info" title="暂未记录该课程的培养方案归属">
              </Notice>
            )}
          </section>

          <section aria-labelledby="course-offerings">
            <h2 id="course-offerings" className="dn-display text-2xl">
              开课范围
            </h2>
            {offeringsResult.error ? (
              <div className="mt-3">
                <Notice tone="warning" title="开课实例暂时无法加载">
                  {offeringsResult.error.message}
                </Notice>
              </div>
            ) : offeringsResult.data && offeringsResult.data.length > 0 ? (
              <ul className="mt-3 grid gap-3 sm:grid-cols-2">
                {offeringsResult.data.map((offering) => (
                  <li key={offering.id} className="border border-divider bg-surface p-4">
                    <p className="font-medium text-text-primary">
                      {offering.academic_year} · {seasonLabels[offering.season] ?? offering.season}
                      {offering.section_code ? ` · ${offering.section_code} 班` : ''}
                    </p>
                    <p className="mt-1 text-sm text-text-secondary">
                      教师：
                      {offering.teachers.length > 0
                        ? offering.teachers.map((teacher) => teacher.display_name).join('、')
                        : '未记录'}
                    </p>
                    {offering.textbook && (
                      <p className="mt-1 text-sm text-text-secondary">教材：{offering.textbook}</p>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <div className="mt-3">
                <Notice tone="info" title="暂未记录开课实例">
                  该课程的资料可能跨学期通用，未绑定到具体开课实例。
                </Notice>
              </div>
            )}
          </section>


        </div>
      </details>
    </div>
  );
}
