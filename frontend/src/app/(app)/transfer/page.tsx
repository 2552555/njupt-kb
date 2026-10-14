import type { Metadata } from 'next';

import { PlannedFeature } from '@/components/common/PlannedFeature';

export const metadata: Metadata = { title: '转专业专题（待评估）' };

export default function TransferPage() {
  return (
    <PlannedFeature
      title="转专业专题"
      summary="转专业涉及课程替代与学业资格判断，需要另有数据与规则来源，首期不做静态示例规则驱动的决策结论。"
    />
  );
}
