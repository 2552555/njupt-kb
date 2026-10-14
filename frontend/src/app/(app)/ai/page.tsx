import type { Metadata } from 'next';

import { PlannedFeature } from '@/components/common/PlannedFeature';

export const metadata: Metadata = { title: 'AI 辅助（待评估）' };

export default function AiPage() {
  return (
    <PlannedFeature
      title="AI 辅助"
      summary="摘要、标签与课程问答属于后续阶段能力，需要质量评估、来源引用与成本控制。功能关闭时界面明确不可用，不返回模拟结果。"
    />
  );
}
