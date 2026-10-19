import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';

import { Notice } from '@/components/common/Notice';
import { serverFetch } from '@/lib/api/server';
import type { KaoyanMajor, KaoyanSchool, KaoyanScoreLine } from '@/lib/api/topics';
import type { CursorPage } from '@/lib/api/types';
import { isUuid } from '@/lib/utils';

export const dynamic = 'force-dynamic';

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params;
  if (!isUuid(id)) return { title: '考研院校' };
  const result = await serverFetch<CursorPage<KaoyanSchool>>('/kaoyan/schools?limit=100');
  const school = result.data?.items.find((item) => item.id === id);
  return { title: school ? `${school.name} 考研` : '考研院校' };
}

export default async function KaoyanSchoolPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!isUuid(id)) notFound();

  const [schoolsResult, majorsResult, linesResult] = await Promise.all([
    serverFetch<CursorPage<KaoyanSchool>>('/kaoyan/schools?limit=100'),
    serverFetch<CursorPage<KaoyanMajor>>(`/kaoyan/schools/${id}/majors?limit=50`),
    serverFetch<CursorPage<KaoyanScoreLine>>(`/kaoyan/score-lines?school_id=${id}&limit=50`),
  ]);

  const school = schoolsResult.data?.items.find((item) => item.id === id);
  const majors = majorsResult.data?.items ?? [];
  const lines = linesResult.data?.items ?? [];
  const majorNames = new Map(majors.map((item) => [item.id, item.name]));

  return (
    <div className="space-y-8">
      <header className="border-b border-divider pb-6">
        <Link href="/kaoyan" className="text-sm text-brand underline underline-offset-2">返回考研专题</Link>
        <h1 className="dn-display mt-4 text-3xl">{school?.name ?? '院校详情'}</h1>
        {school?.website_url && (
          <a
            href={school.website_url}
            target="_blank"
            rel="noopener noreferrer nofollow"
            className="mt-2 inline-block text-sm text-brand underline underline-offset-2"
          >
            研究生院网站
          </a>
        )}
      </header>

      <section aria-labelledby="kaoyan-majors" className="space-y-3">
        <h2 id="kaoyan-majors" className="text-xl font-semibold">招生专业</h2>
        {majorsResult.error ? (
          <Notice tone="warning" title="专业暂时无法加载">{majorsResult.error.message}</Notice>
        ) : majors.length === 0 ? (
          <Notice tone="info" title="还没有专业记录">该院校的专业与考试科目尚未录入。</Notice>
        ) : (
          <ul className="divide-y divide-divider border border-divider bg-surface">
            {majors.map((major) => (
              <li key={major.id} className="p-4">
                <h3 className="text-base font-medium">{major.name}</h3>
                {major.college && <p className="mt-1 text-sm text-text-secondary">{major.college}</p>}
                {major.exam_subjects && (
                  <p className="mt-2 text-sm">
                    考试科目：
                    {Object.entries(major.exam_subjects)
                      .map(([key, value]) => `${key} ${String(value)}`)
                      .join(' · ')}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="kaoyan-lines" className="space-y-3">
        <h2 id="kaoyan-lines" className="text-xl font-semibold">历年分数线</h2>
        {lines.length === 0 ? (
          <Notice tone="info" title="还没有分数线记录">分数线需要按年份与专业录入，并标注来源。</Notice>
        ) : (
          <div className="overflow-x-auto border border-divider bg-surface">
            <table className="w-full border-collapse text-sm">
              <caption className="sr-only">按专业与年份记录的分数线</caption>
              <thead>
                <tr className="bg-canvas text-left">
                  <th scope="col" className="border-b border-divider px-3 py-2">年份</th>
                  <th scope="col" className="border-b border-divider px-3 py-2">专业</th>
                  <th scope="col" className="border-b border-divider px-3 py-2">总分线</th>
                  <th scope="col" className="border-b border-divider px-3 py-2">单科线</th>
                  <th scope="col" className="border-b border-divider px-3 py-2">报考 / 录取</th>
                  <th scope="col" className="border-b border-divider px-3 py-2">最低 / 最高</th>
                  <th scope="col" className="border-b border-divider px-3 py-2">来源</th>
                </tr>
              </thead>
              <tbody>
                {lines.map((line) => (
                  <tr key={line.id}>
                    <td className="border-b border-divider px-3 py-2">{line.year}</td>
                    <td className="border-b border-divider px-3 py-2">
                      {majorNames.get(line.kaoyan_major_id) ?? '专业信息待补充'}
                    </td>
                    <td className="border-b border-divider px-3 py-2">{line.total_score_line ?? '—'}</td>
                    <td className="border-b border-divider px-3 py-2">
                      {[line.political_line, line.english_line, line.business1_line, line.business2_line]
                        .map((value) => value ?? '—')
                        .join(' / ')}
                    </td>
                    <td className="border-b border-divider px-3 py-2">
                      {line.applicants ?? '—'} / {line.admitted ?? '—'}
                    </td>
                    <td className="border-b border-divider px-3 py-2">
                      {line.lowest_score ?? '—'} / {line.highest_score ?? '—'}
                    </td>
                    <td className="border-b border-divider px-3 py-2">
                      {line.source_url ? (
                        <a
                          href={line.source_url}
                          target="_blank"
                          rel="noopener noreferrer nofollow"
                          className="text-brand underline underline-offset-2"
                        >
                          原文
                        </a>
                      ) : (
                        '未标注'
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <Notice tone="info" title="使用边界">
        分数线是历史记录，不构成对下一年度的预测。来源未标注的记录请谨慎参考。
      </Notice>
    </div>
  );
}
