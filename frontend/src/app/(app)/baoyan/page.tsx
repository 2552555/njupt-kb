import type { Metadata } from 'next';
import Link from 'next/link';

import { Notice } from '@/components/common/Notice';
import { serverFetch } from '@/lib/api/server';
import type { BaoyanExperience, BaoyanPolicy, CollegeBrief } from '@/lib/api/topics';
import type { CursorPage } from '@/lib/api/types';
import { formatDate } from '@/lib/utils';

export const metadata: Metadata = { title: '保研专题' };
export const dynamic = 'force-dynamic';

export default async function BaoyanPage() {
  const [policiesResult, experiencesResult, collegesResult] = await Promise.all([
    serverFetch<CursorPage<BaoyanPolicy>>('/baoyan/policies?limit=20'),
    serverFetch<CursorPage<BaoyanExperience>>('/baoyan/experiences?limit=12'),
    serverFetch<CollegeBrief[]>('/colleges'),
  ]);

  const collegeNames = new Map((collegesResult.data ?? []).map((item) => [item.id, item.name]));
  const policies = policiesResult.data?.items ?? [];
  const experiences = experiencesResult.data?.items ?? [];

  return (
    <div className="space-y-8">
      <header className="border-b border-divider pb-6">
        <p className="text-sm text-text-secondary">推免 / 保研</p>
        <h1 className="dn-display mt-2 text-3xl">保研专题</h1>
        <p className="mt-3 max-w-3xl text-text-secondary">
          政策按学院与年份记录，经验帖按目标院校与结果记录。政策是否适用于你，取决于当年文件与学院口径，
          请以来源链接的原文为准；经验帖是个人经历，不是录取承诺。
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          <Link href="/baoyan/submit" className="inline-flex min-h-11 items-center bg-brand px-4 text-sm font-medium text-on-color">
            分享保研经验
          </Link>
          <Link href="/me/guides" className="inline-flex min-h-11 items-center border border-control-border px-4 text-sm">
            写课程攻略
          </Link>
        </div>
      </header>

      <section aria-labelledby="baoyan-policies" className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="baoyan-policies" className="text-xl font-semibold">政策与名额</h2>
          <p className="text-xs text-text-secondary">按记录时间倒序；未标注来源的政策不作结论</p>
        </div>
        {policiesResult.error ? (
          <Notice tone="warning" title="政策暂时无法加载">{policiesResult.error.message}</Notice>
        ) : policies.length === 0 ? (
          <Notice tone="info" title="还没有政策记录">政策数据需要管理员按学院与年份录入并标注来源。</Notice>
        ) : (
          <ul className="divide-y divide-divider border border-divider bg-surface">
            {policies.map((policy) => (
              <li key={policy.id} className="space-y-2 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="bg-brand px-2 py-0.5 text-xs text-on-color">{policy.year} 年</span>
                  <h3 className="text-base font-medium">
                    {collegeNames.get(policy.college_id) ?? '学院信息待补充'}
                  </h3>
                  <span className="border border-divider px-2 py-0.5 text-xs text-text-secondary">
                    {policy.is_verified ? '来源已核对' : '来源未核对'}
                  </span>
                </div>
                <dl className="grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
                  <div>
                    <dt className="text-text-secondary">最低绩点</dt>
                    <dd>{policy.gpa_min ?? '未记录'}</dd>
                  </div>
                  <div>
                    <dt className="text-text-secondary">排名要求</dt>
                    <dd>{policy.gpa_rank_requirement ?? '未记录'}</dd>
                  </div>
                  <div>
                    <dt className="text-text-secondary">总名额</dt>
                    <dd>{policy.total_quota ?? '未记录'}</dd>
                  </div>
                  <div>
                    <dt className="text-text-secondary">常规 / 专项</dt>
                    <dd>
                      {policy.regular_quota ?? '未记录'} / {policy.special_quota ?? '未记录'}
                    </dd>
                  </div>
                </dl>
                {policy.comprehensive_formula && (
                  <p className="text-sm text-text-secondary">综合成绩计算：{policy.comprehensive_formula}</p>
                )}
                {policy.source_url && (
                  <a
                    href={policy.source_url}
                    target="_blank"
                    rel="noopener noreferrer nofollow"
                    className="inline-block text-sm text-brand underline underline-offset-2"
                  >
                    查看来源原文
                  </a>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="baoyan-experiences" className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="baoyan-experiences" className="text-xl font-semibold">经验帖</h2>
          <p className="text-xs text-text-secondary">作者自述，未经平台事实核验</p>
        </div>
        {experiencesResult.error ? (
          <Notice tone="warning" title="经验帖暂时无法加载">{experiencesResult.error.message}</Notice>
        ) : experiences.length === 0 ? (
          <Notice tone="info" title="还没有经验帖">成为第一个分享的人：说明目标院校、时间线与面试形式，比结论更有用。</Notice>
        ) : (
          <ul className="grid gap-4 sm:grid-cols-2">
            {experiences.map((item) => (
              <li key={item.id} className="border border-divider bg-surface p-4">
                <Link href={`/baoyan/experiences/${item.id}`} className="text-base font-medium underline underline-offset-2">
                  {item.year} 年 · {item.target_university}
                  {item.target_major ? ` · ${item.target_major}` : ''}
                </Link>
                <p className="mt-2 text-sm text-text-secondary">
                  结果：{item.offer_result ?? '未填写'} · 本校排名：{item.gpa_rank ?? '未填写'}
                </p>
                {item.advice && <p className="mt-2 line-clamp-3 text-sm">{item.advice}</p>}
                <p className="mt-2 text-xs text-text-secondary">
                  {formatDate(item.created_at)} · 浏览 {item.view_count}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <Notice tone="info" title="使用边界">
        平台不提供保研资格认定或录取预测。政策条目缺失、年份不符或来源未核对时，请直接联系学院教务确认。
      </Notice>
    </div>
  );
}
