import type { Metadata } from 'next';
import { Suspense } from 'react';

import { SearchClient } from '@/components/search/SearchClient';
import { SkeletonList } from '@/components/ui/Skeleton';

export const metadata: Metadata = {
  title: '搜索',
  description: '搜索课程资料：按课程名、代码、资料标题或教师姓名查找已发布内容。',
  // 搜索结果为动态查询，不进入索引（§19.3）。
  robots: { index: false, follow: true },
};

export const dynamic = 'force-dynamic';

export default function SearchPage() {
  return (
    <Suspense fallback={<SkeletonList count={3} />}>
      <SearchClient />
    </Suspense>
  );
}
