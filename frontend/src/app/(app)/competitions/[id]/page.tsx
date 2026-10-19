import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';

import { Notice } from '@/components/common/Notice';
import { serverFetch } from '@/lib/api/server';
import type { Competition } from '@/lib/api/topics';
import { isUuid } from '@/lib/utils';

export const dynamic = 'force-dynamic';

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params;
  if (!isUuid(id)) return { title: '竞赛详情' };
  const result = await serverFetch<Competition>(`/competitions/${id}`);
  return { title: result.data?.name ?? '竞赛详情' };
}

function listText(value: unknown): string {
  if (Array.isArray(value)) return value.map((item) => String(item)).join('、');
  if (value && typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => `${key} ${String(item)}`)
      .join('、');
  }
  return '';
}

export default async function CompetitionDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!isUuid(id)) notFound();
  const result = await serverFetch<Competition>(`/competitions/${id}`);
  if (result.error?.status === 404) notFound();
  const item = result.data;
  if (!item) return <Notice tone="warning" title="竞赛暂时无法加载">{result.error?.message ?? '请稍后重试。'}</Notice>;

  const winners = item.winners ?? [];

  return (
    <div className="space-y-8">
      <header className="border-b border-divider pb-6">
        <Link href="/competitions" className="text-sm text-brand underline underline-offset-2">返回竞赛专题</Link>
        <h1 className="dn-display mt-4 text-3xl">{item.name}</h1>
        <div className="mt-3 flex flex-wrap gap-2 text-sm text-text-secondary">
          {item.category && <span className="bg-brand px-2 py-0.5 text-xs text-on-color">{item.category}</span>}
          {item.organizer && <span>主办：{item.organizer}</span>}
          {item.difficulty !== null && <span>难度 {item.difficulty}/5</span>}
        </div>
        {item.official_url && (
          <a
            href={item.official_url}
            target="_blank"
            rel="noopener noreferrer nofollow"
            className="mt-3 inline-block text-sm text-brand underline underline-offset-2"
          >
            官方通知
          </a>
        )}
      </header>

      {item.description && (
        <section aria-label="竞赛简介" className="space-y-2">
          <h2 className="text-xl font-semibold">简介</h2>
          <p className="whitespace-pre-wrap leading-7">{item.description}</p>
        </section>
      )}

      <section aria-label="参赛信息" className="space-y-2">
        <h2 className="text-xl font-semibold">参赛信息</h2>
        <dl className="grid gap-3 text-sm sm:grid-cols-2">
          {[
            ['报名时间', item.registration_period],
            ['比赛时间', item.competition_period],
            ['队伍规模', item.team_size],
            ['适用专业', listText(item.suitable_majors)],
            ['适用年级', listText(item.suitable_grades)],
            ['校内联系人', item.njupt_contact],
          ].map(([label, value]) => (
            <div key={String(label)} className="border border-divider bg-surface p-3">
              <dt className="text-text-secondary">{label}</dt>
              <dd>{value ? String(value) : '未记录'}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section aria-labelledby="competition-winners" className="space-y-3">
        <h2 id="competition-winners" className="text-xl font-semibold">本校获奖记录</h2>
        {winners.length === 0 ? (
          <Notice tone="info" title="还没有获奖记录">获奖信息需要按年份与奖项录入并核对。</Notice>
        ) : (
          <ul className="divide-y divide-divider border border-divider bg-surface">
            {winners.map((winner) => (
              <li key={winner.id} className="space-y-1 p-4">
                <p className="text-base font-medium">
                  {winner.year} 年 · {winner.award_level}
                </p>
                {winner.project_title && <p className="text-sm">作品：{winner.project_title}</p>}
                {winner.advisor && <p className="text-sm text-text-secondary">指导教师：{winner.advisor}</p>}
                {winner.team_members ? (
                  <p className="text-sm text-text-secondary">成员：{listText(winner.team_members)}</p>
                ) : null}
                {winner.experience_text && (
                  <p className="mt-2 whitespace-pre-wrap text-sm leading-6">{winner.experience_text}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <Notice tone="info" title="使用边界">
        报名条件、赛程与获奖认定以主办方和学校通知为准；本站整理可能滞后。
      </Notice>
    </div>
  );
}
