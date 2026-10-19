import type { Metadata } from 'next';
import Link from 'next/link';

import { Notice } from '@/components/common/Notice';
import { serverFetch } from '@/lib/api/server';
import type { Competition } from '@/lib/api/topics';
import type { CursorPage } from '@/lib/api/types';

export const metadata: Metadata = { title: '竞赛专题' };
export const dynamic = 'force-dynamic';

export default async function CompetitionsPage({
  searchParams,
}: {
  searchParams: Promise<{ category?: string }>;
}) {
  const { category } = await searchParams;
  const query = category ? `?limit=24&category=${encodeURIComponent(category)}` : '?limit=24';
  const result = await serverFetch<CursorPage<Competition>>(`/competitions${query}`);
  const items = result.data?.items ?? [];
  const categories = Array.from(
    new Set(items.map((item) => item.category).filter((value): value is string => !!value)),
  );

  return (
    <div className="space-y-8">
      <header className="border-b border-divider pb-6">
        <p className="text-sm text-text-secondary">学科竞赛 / 获奖记录</p>
        <h1 className="dn-display mt-2 text-3xl">竞赛专题</h1>
        <p className="mt-3 max-w-3xl text-text-secondary">
          竞赛信息按「主办方 + 时间窗口 + 适用专业」记录。报名时间与规则以官方通知为准，
          本站只做整理与经验汇总。
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          <Link href="/search/resources?q=竞赛" className="inline-flex min-h-11 items-center bg-brand px-4 text-sm font-medium text-on-color">
            找竞赛资料
          </Link>
          <Link href="/me/guides" className="inline-flex min-h-11 items-center border border-control-border px-4 text-sm">
            写参赛经验
          </Link>
        </div>
      </header>

      {categories.length > 0 && (
        <nav aria-label="竞赛分类" className="flex flex-wrap gap-2">
          <Link
            href="/competitions"
            className={`inline-flex min-h-11 items-center border px-3 text-sm ${
              category ? 'border-control-border' : 'border-brand bg-brand text-on-color'
            }`}
          >
            全部
          </Link>
          {categories.map((item) => (
            <Link
              key={item}
              href={`/competitions?category=${encodeURIComponent(item)}`}
              className={`inline-flex min-h-11 items-center border px-3 text-sm ${
                category === item ? 'border-brand bg-brand text-on-color' : 'border-control-border'
              }`}
            >
              {item}
            </Link>
          ))}
        </nav>
      )}

      {result.error ? (
        <Notice tone="warning" title="竞赛暂时无法加载">{result.error.message}</Notice>
      ) : items.length === 0 ? (
        <Notice tone="info" title="还没有竞赛记录">
          竞赛条目由管理员按来源录入；没有记录时不展示未经核实的信息。
        </Notice>
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2">
          {items.map((item) => (
            <li key={item.id} className="border border-divider bg-surface p-4">
              <div className="flex flex-wrap items-center gap-2">
                {item.category && (
                  <span className="bg-brand px-2 py-0.5 text-xs text-on-color">{item.category}</span>
                )}
                {item.difficulty !== null && (
                  <span className="border border-divider px-2 py-0.5 text-xs text-text-secondary">
                    难度 {item.difficulty}/5
                  </span>
                )}
              </div>
              <Link
                href={`/competitions/${item.id}`}
                className="mt-2 block text-base font-medium underline underline-offset-2"
              >
                {item.name}
              </Link>
              {item.organizer && <p className="mt-1 text-sm text-text-secondary">{item.organizer}</p>}
              {item.description && <p className="mt-2 line-clamp-3 text-sm">{item.description}</p>}
              <dl className="mt-3 grid gap-1 text-xs text-text-secondary">
                {item.registration_period && <div>报名：{item.registration_period}</div>}
                {item.competition_period && <div>比赛：{item.competition_period}</div>}
                {item.team_size && <div>队伍规模：{item.team_size}</div>}
                {item.njupt_winners_count !== null && <div>本校获奖记录：{item.njupt_winners_count} 条</div>}
              </dl>
            </li>
          ))}
        </ul>
      )}

      <Notice tone="info" title="使用边界">
        报名截止、参赛资格与获奖认定以主办方与学校通知为准。本站记录可能滞后，请勿只依据本站安排报名。
      </Notice>
    </div>
  );
}
