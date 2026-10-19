'use client';

import Link from 'next/link';
import { useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Activity } from 'lucide-react';

import { TrendChart } from '@/components/admin/TrendChart';
import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { SkeletonCard, SkeletonList } from '@/components/ui/Skeleton';
import {
  getAnalyticsOverview,
  type AnalyticsFunnel,
  type AnalyticsOverview,
} from '@/lib/api/analytics';
import { ApiError, describeError } from '@/lib/api/client';
import { formatDateTime } from '@/lib/utils';
import { hasPermission, useAuthStore } from '@/stores/authStore';

const WINDOWS = [7, 14, 30] as const;
type WindowDays = (typeof WINDOWS)[number];

const FUNNEL_STAGES: { key: keyof AnalyticsFunnel; label: string; color: string }[] = [
  { key: 'uploaded', label: '上传会话', color: 'bg-brand' },
  { key: 'submitted', label: '提交版本', color: 'bg-cyan' },
  { key: 'approved', label: '审核通过', color: 'bg-emerald' },
  { key: 'published', label: '发布', color: 'bg-violet' },
  { key: 'downloaded', label: '下载授权', color: 'bg-steel' },
];

export default function AdminAnalyticsPage() {
  const user = useAuthStore((s) => s.user);
  return hasPermission(user, 'metrics.view') ? <AnalyticsDashboard /> : <Notice title="需要管理员权限" />;
}

function AnalyticsDashboard() {
  const user = useAuthStore((s) => s.user);
  const [days, setDays] = useState<WindowDays>(14);
  const query = useQuery({
    queryKey: ['admin-analytics', user?.id, days],
    queryFn: () => getAnalyticsOverview(days),
  });

  const data = query.data;
  let body: ReactNode;

  if (query.isLoading) {
    body = (
      <div className="space-y-6" role="status" aria-label="分析数据加载中">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, index) => (
            <SkeletonCard key={index} />
          ))}
        </div>
        <SkeletonList count={2} />
      </div>
    );
  } else if (query.error) {
    const status = query.error instanceof ApiError ? query.error.status : 0;
    body =
      status === 403 ? (
        <Notice tone="warning" title="没有查看数据分析的权限">
          当前账号缺少 METRICS_VIEW 权限。请联系管理员分配权限后再打开本页；其他后台页面不受影响。
        </Notice>
      ) : (
        <Notice
          tone="warning"
          title="分析数据暂时无法加载"
          action={
            <Button variant="secondary" onClick={() => query.refetch()}>
              重试
            </Button>
          }
        >
          {describeError(query.error).message}
        </Notice>
      );
  } else if (!data) {
    body = <Notice tone="info" title="暂无分析数据" />;
  } else {
    body = <AnalyticsBody data={data} />;
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="dn-display text-2xl">运营总览</h2>
          <p className="mt-1 text-sm text-text-secondary">
            只读聚合 · 按 UTC 自然日分桶
            {data ? ` · 生成于 ${formatDateTime(data.generated_at)}` : ''}
          </p>
        </div>
        <div role="group" aria-label="统计窗口" className="flex border border-control-border">
          {WINDOWS.map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={days === value}
              onClick={() => setDays(value)}
              className={`min-h-11 px-4 text-sm font-medium ${
                days === value
                  ? 'bg-brand text-on-color'
                  : 'bg-surface text-text-secondary hover:bg-canvas hover:text-text-primary'
              }`}
            >
              {value} 天
            </button>
          ))}
        </div>
      </div>

      {body}
    </div>
  );
}

function AnalyticsBody({ data }: { data: AnalyticsOverview }) {
  const dates = data.series.map((point) => point.date);

  return (
    <div className="space-y-8">
      <section aria-label="窗口内关键指标" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <MetricTile
          label="新增用户"
          value={data.totals.new_users}
          hint="当日新建且当前未注销的账号"
          accent="border-t-brand"
        />
        <MetricTile
          label="新增资料"
          value={data.totals.new_resources}
          hint="新建资源行，任何发布状态都计入"
          accent="border-t-cyan"
        />
        <MetricTile
          label="发布版本"
          value={data.totals.published_versions}
          hint="审计 resource.published，发布与回滚各计一次"
          accent="border-t-emerald"
        />
        <MetricTile
          label="下载授权"
          value={data.totals.downloads}
          hint="已签发的授权，含后续被撤销的授权"
          accent="border-t-violet"
        />
        <MetricTile
          label="举报"
          value={data.totals.reports}
          hint="新建举报工单"
          accent="border-t-amber"
        />
        <MetricTile
          label="案件"
          value={data.totals.cases}
          hint="新建案件"
          accent="border-t-crimson"
        />
      </section>

      <section aria-label="趋势图" className="grid gap-4 xl:grid-cols-2">
        <TrendChart
          title="内容供给"
          description="每日新增资料与进入发布状态的版本数。"
          dates={dates}
          kind="bar"
          series={[
            {
              label: '新增资料',
              color: 'var(--dn-emerald)',
              values: data.series.map((point) => point.new_resources),
            },
            {
              label: '发布版本',
              color: 'var(--dn-teal)',
              values: data.series.map((point) => point.published_versions),
            },
          ]}
          footer="柱子高度按窗口内峰值归一；悬停单日柱体可查看数值。"
        />
        <TrendChart
          title="内容消费"
          description="每日签发的下载授权与新增收藏。"
          dates={dates}
          kind="line"
          series={[
            {
              label: '下载授权',
              color: 'var(--dn-violet)',
              values: data.series.map((point) => point.downloads),
            },
            {
              label: '新增收藏',
              color: 'var(--dn-amber)',
              values: data.series.map((point) => point.favorites),
            },
          ]}
          footer="折线按窗口内峰值归一；悬停数据点可查看数值。"
        />
      </section>

      <section aria-labelledby="analytics-funnel" className="space-y-3">
        <div>
          <h2 id="analytics-funnel" className="dn-display text-xl">
            转化漏斗
          </h2>
          <p className="mt-1 text-xs text-text-secondary">
            窗口内绝对量。各阶段口径与分母不同，只做绝对量展示，不计算「用户转化率」。
          </p>
        </div>
        <Funnel funnel={data.funnel} />
      </section>

      <section aria-labelledby="analytics-top-courses" className="space-y-3">
        <h2 id="analytics-top-courses" className="dn-display text-xl">
          下载领先课程
        </h2>
        {data.top_courses.length === 0 ? (
          <Notice tone="info" title="窗口内暂无下载数据">
            下载授权产生后，这里会按课程汇总排名。
          </Notice>
        ) : (
          <div className="overflow-x-auto border border-divider">
            <table className="w-full min-w-[480px] border-collapse text-sm">
              <caption className="sr-only">窗口内下载授权数领先的课程</caption>
              <thead>
                <tr className="bg-canvas text-left text-text-secondary">
                  <th scope="col" className="px-4 py-3 font-medium">
                    排名
                  </th>
                  <th scope="col" className="px-4 py-3 font-medium">
                    课程
                  </th>
                  <th scope="col" className="px-4 py-3 font-medium">
                    下载数
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.top_courses.map((course, index) => (
                  <tr key={course.course_id} className="border-t border-divider">
                    <td className="px-4 py-3 text-text-secondary">{index + 1}</td>
                    <td className="px-4 py-3">
                      <Link
                        href={`/courses/${course.course_id}`}
                        className="inline-flex min-h-11 items-center text-brand underline"
                      >
                        {course.name}
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-text-primary">{course.download_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section
        aria-labelledby="analytics-notes"
        className="border border-divider bg-canvas p-4"
      >
        <h2 id="analytics-notes" className="dn-display text-lg">
          口径说明
        </h2>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-text-secondary">
          {data.data_notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      </section>

      <p className="flex items-center gap-2 text-xs text-text-secondary">
        <Activity className="h-4 w-4" aria-hidden="true" />
        只读聚合 · 窗口 {data.window_days} 天 · 生成于 {formatDateTime(data.generated_at)}
      </p>
    </div>
  );
}

function MetricTile({
  label,
  value,
  hint,
  accent,
}: {
  label: string;
  value: number;
  hint: string;
  accent: string;
}) {
  return (
    <div className={`border border-divider border-t-4 bg-surface p-4 ${accent}`}>
      <span className="dn-kicker block text-text-secondary">{label}</span>
      <span className="dn-display mt-1 block text-3xl text-text-primary">{value}</span>
      <span className="mt-1 block text-xs text-text-secondary">{hint}</span>
    </div>
  );
}

function Funnel({ funnel }: { funnel: AnalyticsFunnel }) {
  const max = Math.max(...FUNNEL_STAGES.map((stage) => funnel[stage.key]), 0);

  return (
    <ol className="space-y-4 border border-divider bg-surface p-4">
      {FUNNEL_STAGES.map((stage, index) => {
        const value = funnel[stage.key];
        const previous = index === 0 ? null : funnel[FUNNEL_STAGES[index - 1].key];
        const ratio =
          previous === null ? null : previous === 0 ? '—' : `${Math.round((value / previous) * 100)}%`;
        const width = max > 0 ? (value / max) * 100 : 0;

        return (
          <li key={stage.key}>
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="text-sm font-medium text-text-primary">{stage.label}</span>
              <span className="flex items-baseline gap-3">
                <span className="dn-display text-xl text-text-primary">{value}</span>
                <span className="text-xs text-text-secondary">
                  {index === 0 ? '起点' : `较上一阶段 ${ratio}`}
                </span>
              </span>
            </div>
            <div className="mt-1 h-4 w-full bg-canvas" aria-hidden="true">
              <div
                className={`h-full ${stage.color}`}
                style={{ width: `${value > 0 ? Math.max(width, 2) : 0}%` }}
              />
            </div>
          </li>
        );
      })}
    </ol>
  );
}
