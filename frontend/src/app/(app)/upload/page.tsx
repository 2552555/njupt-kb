import type { Metadata } from 'next';

import { AuthGuard } from '@/components/layout/AuthGuard';
import { Notice } from '@/components/common/Notice';
import type { CourseDetail } from '@/lib/api/catalog';
import { serverFetch } from '@/lib/api/server';
import { isUuid } from '@/lib/utils';
import { UploadFlow } from '@/components/upload/UploadFlow';
import { isResourceType } from '@/lib/api/types';

export const metadata: Metadata = {
  title: '上传资料',
  robots: { index: false, follow: false },
};

export const dynamic = 'force-dynamic';

export default async function UploadPage({ searchParams }: {
  searchParams: Promise<{ course_id?: string; type?: string; request_id?: string }>;
}) {
  const { course_id, type, request_id } = await searchParams;
  const initialType = type && isResourceType(type) ? type : 'note';
  const courseResult = course_id && isUuid(course_id)
    ? await serverFetch<CourseDetail>(`/courses/${course_id}`) : null;
  return (
    <AuthGuard>
      <div className="space-y-6">
        <header className="bg-ink px-6 py-8 text-on-ink">
          <p className="dn-kicker">UPLOAD</p>
          <h1 className="dn-display mt-2 text-3xl">上传资料</h1>
          <p className="mt-2 max-w-2xl text-on-ink">
            未完成时可以保存草稿，稍后继续。
          </p>
        </header>
        {course_id && !courseResult?.data && <Notice tone="warning" title="未能带入课程">请重新搜索并选择课程。</Notice>}
        <UploadFlow key={`${course_id ?? 'new'}-${initialType}`} initialCourse={courseResult?.data ?? null} initialType={initialType} requestId={request_id && isUuid(request_id) ? request_id : undefined} />
      </div>
    </AuthGuard>
  );
}
