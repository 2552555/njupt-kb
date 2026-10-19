import type { Metadata } from 'next';

import { Notice } from '@/components/common/Notice';

const PLATFORM = '南邮课程知识平台';
const LAST_UPDATED = '2026-10-16';

export const metadata: Metadata = {
  title: '隐私政策',
  description: '南邮课程知识平台的隐私政策：我们收集哪些信息、如何使用与保护。',
};

const sections = [
  {
    title: '一、我们收集的信息',
    body: '账号注册与登录所需的信息（如邮箱）、你主动提交的内容（资料、评论、需求）、以及平台运行所需的日志信息（访问时间、操作记录）。',
  },
  {
    title: '二、信息的使用',
    body: '用于账号管理、内容审核、侵权处理、安全风控与平台运营分析；不会用于你未授权的用途。',
  },
  {
    title: '三、信息的共享与披露',
    body: '仅在法律法规要求、权利人投诉处理或你明确授权的场景下向第三方提供；不会出售个人信息。',
  },
  {
    title: '四、数据存储与安全',
    body: '实际运营主体、数据存储位置、服务提供方及各类数据保存期限仍待确认。应用已提供访问控制与审计功能，实际部署与安全措施需单独核实。',
  },
  {
    title: '五、你的权利',
    body: '可在「账号与安全」中修改昵称、导出本人数据和注销账号。注销会撤销会话并匿名化账号资料，既有业务记录仍保留；自动到期清理尚未启用。',
  },
  {
    title: '六、联系我们',
    body: '运营主体及隐私事务联系方式待确认后公布。资料问题可使用资料详情中的「举报或纠错」入口；该入口不替代正式隐私联系渠道。',
  },
];

export default function PrivacyPage() {
  return (
    <div className="space-y-6">
      <header>
        <p className="dn-kicker text-text-secondary">PRIVACY</p>
        <h1 className="dn-display mt-2 text-3xl">隐私政策</h1>
        <p className="mt-2 text-sm text-text-secondary">
          {PLATFORM} · 最后更新：{LAST_UPDATED}
        </p>
      </header>

      <Notice tone="info" title="Beta 草案，尚未生效">
        本页用于梳理拟定事项，不代表已生效的隐私政策。实际运营主体、存储位置、联系方式及正式条款待确认后公布；下方拟定原则不构成当前部署情况的证明。
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
