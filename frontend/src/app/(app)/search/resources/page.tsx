import type { Metadata } from 'next';
import { Suspense } from 'react';

import { ResourceSearchClient } from '@/components/search/ResourceSearchClient';
import { SkeletonList } from '@/components/ui/Skeleton';

export const metadata: Metadata = {
  title: '资料筛选',
  description: '按关键词、资料类型与适用年份筛选已发布课程资料。',
  // 筛选结果为动态查询，不进入索引（§19.3）。
  robots: { index: false, follow: true },
};

export const dynamic = 'force-dynamic';

export default function ResourceSearchPage() {
  return (
    <Suspense fallback={<SkeletonList count={3} />}>
      <ResourceSearchClient />
    </Suspense>
  );
}
