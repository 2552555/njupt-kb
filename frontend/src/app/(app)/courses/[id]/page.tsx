import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';

import { Notice } from '@/components/common/Notice';
import { ResourceCard } from '@/components/resource/ResourceCard';
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
import { isUuid } from '@/lib/utils';

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
  searchParams: Promise<{ type?: string }>;
}) {
  const { id } = await params;
  if (!isUuid(id)) notFound();

  const { type } = await searchParams;
  const typeFilter: ResourceType | undefined =
    type && isResourceType(type) ? type : undefined;

  const courseResult = await loadCourse(id);
  if (courseResult.error?.status === 404) notFound();
  const course = courseResult.data;

  const [offeringsResult, resourcesResult] = await Promise.all([
    serverFetch<OfferingItem[]>(`/courses/${id}/offerings`),
    serverFetch<CursorPage<ResourceListItem>>(
      `/resources?course_id=${id}${typeFilter ? `&resource_type=${typeFilter}` : ''}&limit=20`,
    ),
  ]);

  if (!course) {
    return (
      <Notice tone="warning" title="课程信息暂时无法加载">
        {courseResult.error?.message ?? '请稍后重试。'}
      </Notice>
    );
  }

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
            课程可能属于公共课或尚未完成目录维护；资料仍按课程本身关联。
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

      <section aria-labelledby="course-resources">
        <h2 id="course-resources" className="dn-display text-2xl">
          分类资料
        </h2>
        <div className="mt-3 flex flex-wrap gap-2">
          <Link
            href={`/courses/${id}`}
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
              href={`/courses/${id}?type=${resourceType}`}
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

        <div className="mt-4">
          {resourcesResult.error ? (
            <Notice tone="warning" title="资料列表暂时无法加载">
              {resourcesResult.error.message}
            </Notice>
          ) : resourcesResult.data && resourcesResult.data.items.length > 0 ? (
            <div className="grid gap-4 sm:grid-cols-2">
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
                ? '可以切换到其他类型，或查看全部类型。'
                : '已发布资料通过审核后才会显示；你也可以在登录后为这门课上传资料。'}
            </Notice>
          )}
        </div>
      </section>
    </div>
  );
}
