import type { Metadata } from 'next';

import { PlannedFeature } from '@/components/common/PlannedFeature';

export const metadata: Metadata = { title: '考研专题（待评估）' };

export default function KaoyanPage() {
  return (
    <PlannedFeature
      title="考研专题"
      summary="考研资料与课程资料的组织方式不同，需要独立说明适用范围与来源；当前不提供未经核实的政策结论。"
    />
  );
}
