'use client';

import { useQuery } from '@tanstack/react-query';
import { Activity } from 'lucide-react';

import { Notice } from '@/components/common/Notice';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { SkeletonCard, SkeletonList } from '@/components/ui/Skeleton';
import { fetchMetrics } from '@/lib/api/admin';
import { describeError } from '@/lib/api/client';
import { formatDateTime } from '@/lib/utils';
import { hasPermission, useAuthStore } from '@/stores/authStore';

export default function AdminMetricsPage() {
  const user = useAuthStore((s) => s.user);
  return hasPermission(user, 'metrics.view') ? <MetricsOverview /> : <Notice title="需要管理员权限" />;
}

function MetricsOverview() {
  const user = useAuthStore((s) => s.user);
  const query = useQuery({
    queryKey: ['admin-metrics', user?.id],
    queryFn: fetchMetrics,
  });

  if (query.isLoading) {
    return (
      <div className="space-y-6" role="status" aria-label="指标加载中">
        <div className="grid gap-3 sm:grid-cols-3">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
        <SkeletonList count={2} />
      </div>
    );
  }

  if (query.error) {
    return (
      <Notice
        tone="warning"
        title="指标暂时无法加载"
        action={
          <Button variant="secondary" onClick={() => query.refetch()}>
            重试
          </Button>
        }
      >
        {describeError(query.error).message}
      </Notice>
    );
  }

  const data = query.data;
  if (!data) return <Notice tone="info" title="暂无指标数据" />;

  const cacheClasses = Object.entries(data.cache.by_class);

  return (
    <div className="space-y-8">
      <section aria-labelledby="metrics-pool" className="space-y-3">
        <h2 id="metrics-pool" className="dn-display text-xl">
          连接池预算
        </h2>
        <div className="grid gap-3 sm:grid-cols-3">
          <MetricTile label="池大小" value={data.db_pool.pool_size} hint="常驻连接数" />
          <MetricTile label="最大溢出" value={data.db_pool.max_overflow} hint="超出池大小的临时连接" />
          <MetricTile
            label="获取超时"
            value={data.db_pool.pool_timeout_seconds}
            hint="单位：秒"
          />
        </div>
      </section>

      <section aria-labelledby="metrics-cache" className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 id="metrics-cache" className="dn-display text-xl">
            缓存统计
          </h2>
          <Badge color={data.cache.enabled ? 'green' : 'gray'}>
            {data.cache.enabled ? '已启用' : '已关闭'}
          </Badge>
        </div>
        <dl className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <MetricCell label="条目" value={data.cache.entries} />
          <MetricCell label="上限" value={data.cache.max_entries} />
          <MetricCell label="命中" value={data.cache.hits} />
          <MetricCell label="未命中" value={data.cache.misses} />
          <MetricCell label="淘汰" value={data.cache.evictions} />
          <MetricCell label="失效" value={data.cache.invalidations} />
        </dl>
        {cacheClasses.length === 0 ? (
          <p className="text-sm text-text-secondary">暂无分类缓存数据。</p>
        ) : (
          <div className="overflow-x-auto border border-divider">
            <table className="w-full min-w-[480px] border-collapse text-sm">
              <caption className="sr-only">按缓存分类的条目与命中统计</caption>
              <thead>
                <tr className="bg-canvas text-left text-text-secondary">
                  <th scope="col" className="px-4 py-3 font-medium">分类</th>
                  <th scope="col" className="px-4 py-3 font-medium">条目</th>
                  <th scope="col" className="px-4 py-3 font-medium">命中</th>
                  <th scope="col" className="px-4 py-3 font-medium">未命中</th>
                </tr>
              </thead>
              <tbody>
                {cacheClasses.map(([name, stats]) => (
                  <tr key={name} className="border-t border-divider">
                    <td className="px-4 py-3 break-all text-text-primary">{name}</td>
                    <td className="px-4 py-3 text-text-primary">{stats.entries}</td>
                    <td className="px-4 py-3 text-text-primary">{stats.hits}</td>
                    <td className="px-4 py-3 text-text-primary">{stats.misses}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section aria-labelledby="metrics-events" className="space-y-3">
        <h2 id="metrics-events" className="dn-display text-xl">
          域事件积压
        </h2>
        <dl className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <MetricCell label="待处理" value={data.events.pending} />
          <MetricCell label="处理中" value={data.events.processing} />
          <MetricCell label="已处理" value={data.events.processed} />
          <MetricCell label="失败" value={data.events.failed} />
          <MetricCell label="超出重试上限" value={data.events.over_max_attempts} />
          <MetricCell label="重复去重键" value={data.events.duplicate_dedupe_keys} />
        </dl>
      </section>

      <section aria-labelledby="metrics-jobs" className="space-y-3">
        <h2 id="metrics-jobs" className="dn-display text-xl">
          任务积压
        </h2>
        <dl className="grid gap-3 sm:grid-cols-3 lg:grid-cols-7">
          <MetricCell label="排队" value={data.jobs.queued} />
          <MetricCell label="运行中" value={data.jobs.running} />
          <MetricCell label="等待重试" value={data.jobs.retry_wait} />
          <MetricCell label="成功" value={data.jobs.succeeded} />
          <MetricCell label="失败" value={data.jobs.failed} />
          <MetricCell label="已取消" value={data.jobs.cancelled} />
          <MetricCell label="合计" value={data.jobs.total} />
        </dl>
      </section>

      <section aria-labelledby="metrics-slow" className="space-y-3">
        <h2 id="metrics-slow" className="dn-display text-xl">
          慢查询阈值
        </h2>
        <div className="grid gap-3 sm:grid-cols-3">
          <MetricTile label="慢查询阈值" value={data.slow_query_ms} hint="单位：毫秒" />
        </div>
      </section>

      <p className="flex items-center gap-2 text-xs text-text-secondary">
        <Activity className="h-4 w-4" aria-hidden="true" />
        只读聚合 · 生成于 {formatDateTime(data.generated_at)}
      </p>
    </div>
  );
}

function MetricTile({ label, value, hint }: { label: string; value: number; hint: string }) {
  return (
    <div className="border border-divider bg-surface p-4">
      <span className="dn-kicker block text-text-secondary">{label}</span>
      <span className="dn-display mt-1 block text-2xl text-text-primary">{value}</span>
      <span className="mt-1 block text-xs text-text-secondary">{hint}</span>
    </div>
  );
}

function MetricCell({ label, value }: { label: string; value: number }) {
  return (
    <div className="border border-divider bg-surface p-4">
      <dt className="text-sm text-text-secondary">{label}</dt>
      <dd className="dn-display mt-1 text-2xl text-text-primary">{value}</dd>
    </div>
  );
}
