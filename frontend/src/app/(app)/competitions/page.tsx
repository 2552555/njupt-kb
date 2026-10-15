import type { Metadata } from 'next';

import { PlannedFeature } from '@/components/common/PlannedFeature';

export const metadata: Metadata = { title: '竞赛专题（待评估）' };

export default function CompetitionsPage() {
  return (
    <PlannedFeature
      title="竞赛专题"
      summary="竞赛信息时效性强、来源分散，需要稳定的来源标注与更新机制，暂不进入首期主流程。"
    />
  );
}
