import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';

import { Notice } from '@/components/common/Notice';
import { serverFetch } from '@/lib/api/server';
import type { BaoyanExperience } from '@/lib/api/topics';
import { formatDate, isUuid } from '@/lib/utils';

export const dynamic = 'force-dynamic';

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params;
  if (!isUuid(id)) return { title: '保研经验' };
  const result = await serverFetch<BaoyanExperience>(`/baoyan/experiences/${id}`);
  return { title: result.data ? `${result.data.target_university} 保研经验` : '保研经验' };
}

function text(value: unknown): string | null {
  if (typeof value === 'string' && value.trim()) return value.trim();
  return null;
}

export default async function BaoyanExperiencePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!isUuid(id)) notFound();
  const result = await serverFetch<BaoyanExperience>(`/baoyan/experiences/${id}`);
  if (result.error?.status === 404) notFound();
  const item = result.data;
  if (!item) return <Notice tone="warning" title="经验帖暂时无法加载">{result.error?.message ?? '请稍后重试。'}</Notice>;

  const sections: Array<{ title: string; body: string | null }> = [
    { title: '夏令营经历', body: text(item.summer_camp_experience) },
    { title: '预推免 / 面试经历', body: text(item.pre_interview_experience) },
    { title: '给后来者的建议', body: text(item.advice) },
  ];

  return (
    <article className="space-y-6">
      <header className="border-b border-divider pb-6">
        <Link href="/baoyan" className="text-sm text-brand underline underline-offset-2">返回保研专题</Link>
        <h1 className="dn-display mt-4 text-3xl">
          {item.year} 年 · {item.target_university}
          {item.target_college ? ` · ${item.target_college}` : ''}
        </h1>
        <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-text-secondary">目标专业</dt>
            <dd>{item.target_major ?? '未填写'}</dd>
          </div>
          <div>
            <dt className="text-text-secondary">结果</dt>
            <dd>{item.offer_result ?? '未填写'}</dd>
          </div>
          <div>
            <dt className="text-text-secondary">本校排名</dt>
            <dd>{item.gpa_rank ?? '未填写'}{item.gpa !== null ? ` · 绩点 ${item.gpa}` : ''}</dd>
          </div>
        </dl>
        <p className="mt-3 text-xs text-text-secondary">
          发布于 {formatDate(item.created_at)} · 作者自述，未经平台事实核验
        </p>
      </header>

      {sections.map((section) =>
        section.body ? (
          <section key={section.title} aria-label={section.title} className="space-y-2">
            <h2 className="text-xl font-semibold">{section.title}</h2>
            <p className="whitespace-pre-wrap leading-7">{section.body}</p>
          </section>
        ) : null,
      )}

      {sections.every((section) => !section.body) && (
        <Notice tone="info" title="正文尚未填写">该经验帖只有基本信息，没有可阅读的经历描述。</Notice>
      )}

      <Notice tone="warning" title="请注意时效">
        招生政策、考核形式与名额每年可能变化。经验帖只代表作者当年的个人经历，不能替代当年官方文件。
      </Notice>
    </article>
  );
}
