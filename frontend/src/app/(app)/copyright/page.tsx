import type { Metadata } from 'next';

import { Notice } from '@/components/common/Notice';

const PLATFORM = '南邮课程知识平台';
const LAST_UPDATED = '2026-10-16';

export const metadata: Metadata = {
  title: '版权与侵权投诉',
  description: '南邮课程知识平台的版权声明与侵权投诉处理流程。',
};

const sections = [
  {
    title: '一、版权声明',
    body: '平台上的资料由用户上传并自行声明来源与分发范围；平台尊重知识产权，对涉嫌侵权的内容将依据投诉及时处理。',
  },
  {
    title: '二、侵权投诉流程',
    body: '权利人可通过平台内「举报或纠错」渠道提交投诉，请说明权属证明、侵权内容位置与联系方式，以便核实处理。',
  },
  {
    title: '三、处理时限',
    body: '收到合格投诉后，平台将在合理期限内完成核实并采取下架、限制访问等必要措施，处理结果会通知投诉人与被投诉方。',
  },
  {
    title: '四、重复侵权处理',
    body: '对多次侵权的账号，平台将依据服务条款采取限制上传、暂停或终止服务等措施。',
  },
  {
    title: '五、反通知',
    body: '正式反通知渠道、所需材料与复核规则待确认。当前举报或纠错入口不代表已经建立完整的版权反通知流程，也不保证恢复内容。',
  },
];

export default function CopyrightPage() {
  return (
    <div className="space-y-6">
      <header>
        <p className="dn-kicker text-text-secondary">COPYRIGHT</p>
        <h1 className="dn-display mt-2 text-3xl">版权与侵权投诉</h1>
        <p className="mt-2 text-sm text-text-secondary">
          {PLATFORM} · 最后更新：{LAST_UPDATED}
        </p>
      </header>

      <Notice tone="info" title="Beta 草案，尚未生效">
        本页为拟定流程，实际运营主体、正式投诉联系方式和处理规则待确认后公布。资料详情保留「举报或纠错」入口，不据本草案承诺处理时限或结果。
      </Notice>

      {sections.map((section) => (
        <section key={section.title} className="border border-divider bg-surface p-5">
          <h2 className="text-lg font-semibold text-text-primary">{section.title}</h2>
          <p className="mt-2 text-sm text-text-secondary">{section.body}</p>
        </section>
      ))}
    </div>
  );
}
