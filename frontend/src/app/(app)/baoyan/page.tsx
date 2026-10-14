import type { Metadata } from 'next';

import { PlannedFeature } from '@/components/common/PlannedFeature';

export const metadata: Metadata = { title: '保研专题（待评估）' };

export default function BaoyanPage() {
  return (
    <PlannedFeature
      title="保研专题"
      summary="保研政策与资格判断需要独立的政策年份、来源与适用对象，不与课程实体共用一种模型，因此不进入首期主流程。"
    />
  );
}
