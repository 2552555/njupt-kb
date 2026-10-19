import type { Metadata } from 'next';

import { Notice } from '@/components/common/Notice';

const PLATFORM = '南邮课程知识平台';
const LAST_UPDATED = '2026-10-16';

export const metadata: Metadata = {
  title: '服务条款',
  description: '南邮课程知识平台的服务条款：服务说明、账号规范、内容与版权约定。',
};

const sections = [
  {
    title: '一、服务说明',
    body: '平台按课程组织可追溯的学习资料，提供检索、预览、下载授权与互动功能。部分能力依赖审核与风控，可能随运营策略调整。',
  },
  {
    title: '二、账号与使用规范',
    body: '你对账号下的操作负责；不得利用平台从事刷量、爬取、传播未授权内容等违反法律法规或侵犯他人权益的行为。',
  },
  {
    title: '三、内容与版权',
    body: '上传者应确保对内容拥有合法权利并如实声明来源；平台依据权利人投诉与审核结果处理涉嫌侵权内容，详见「版权与侵权投诉」。',
  },
  {
    title: '四、免责与责任限制',
    body: '资料内容由贡献者提供，仅供参考；平台不对内容的准确性、完整性作担保，在法律允许范围内承担相应责任。',
  },
  {
    title: '五、条款变更',
    body: '正式条款的生效日期、变更通知和确认方式待运营主体与条款审查确认后公布。',
  },
];

export default function TermsPage() {
  return (
    <div className="space-y-6">
      <header>
        <p className="dn-kicker text-text-secondary">TERMS</p>
        <h1 className="dn-display mt-2 text-3xl">服务条款</h1>
        <p className="mt-2 text-sm text-text-secondary">
          {PLATFORM} · 最后更新：{LAST_UPDATED}
        </p>
      </header>

      <Notice tone="info" title="Beta 草案，尚未生效">
        本页为待审查的拟定内容，不代表已生效的服务条款。实际运营主体、联系方式及正式条款待确认后公布。
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
