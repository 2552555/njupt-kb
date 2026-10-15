import Link from 'next/link';

import type { CourseListItem } from '@/lib/api/catalog';

/** 课程条目：名称、代码、归属学院与别名；链接到课程页。 */
export function CourseCard({ course }: { course: CourseListItem }) {
  return (
    <Link
      href={`/courses/${course.id}`}
      className="block border border-divider bg-surface p-4 transition-shadow duration-200 hover:shadow-2"
    >
      <div className="flex items-start justify-between gap-3">
        <h3 className="font-semibold text-text-primary">{course.name}</h3>
        {course.code && (
          <span className="shrink-0 bg-steel px-2 py-0.5 text-xs font-semibold text-on-color">
            {course.code}
          </span>
        )}
      </div>
      <p className="mt-1 text-sm text-text-secondary">
        {course.college_name ?? '归属学院待确认'}
      </p>
      {course.aliases.length > 0 && (
        <p className="mt-2 truncate text-xs text-text-secondary">
          别名：{course.aliases.join('、')}
        </p>
      )}
      <p className="mt-2 text-xs text-text-secondary">
        收录于 {course.curriculum_count} 个培养方案
      </p>
    </Link>
  );
}
