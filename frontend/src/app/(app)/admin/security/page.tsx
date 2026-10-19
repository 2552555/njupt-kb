'use client';

import { useQuery } from '@tanstack/react-query';
import { ShieldAlert } from 'lucide-react';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { SkeletonCard, SkeletonList } from '@/components/ui/Skeleton';
import { describeError } from '@/lib/api/client';
import { fetchSecurityOverview } from '@/lib/api/admin';
import { hasPermission, useAuthStore } from '@/stores/authStore';
import { formatDateTime } from '@/lib/utils';

export default function AdminSecurityPage() {
  const user = useAuthStore((s) => s.user);
  return hasPermission(user, 'audit.view') ? <SecurityOverview /> : <Notice title="需要管理员权限" />;
}

function SecurityOverview() {
  const user = useAuthStore((s) => s.user);
  const query = useQuery({
    queryKey: ['admin-security-overview', user?.id],
    queryFn: fetchSecurityOverview,
  });

  if (query.isLoading) {
    return (
      <div className="space-y-6" role="status" aria-label="安全概览加载中">
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
        title="安全概览暂时无法加载"
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
  if (!data) {
    return <Notice tone="info" title="暂无安全数据" />;
  }

  return (
    <div className="space-y-6">
      <section aria-label="安全指标总览" className="grid gap-3 sm:grid-cols-3">
        <StatCard label="近 1 小时登录失败" value={data.login_failures_last_hour} hint="按窗口内审计计数" />
        <StatCard label="近 24 小时新 IP 登录" value={data.new_ip_login_count} hint="同一账号历史未出现的来源 IP" />
        <StatCard label="近 7 天下载速率告警" value={data.download_velocity_flagged_last_7d} hint="security.download_velocity_flagged 审计" />
      </section>

      <section aria-labelledby="security-login-failures" className="space-y-3">
        <h2 id="security-login-failures" className="dn-display text-xl">
          登录失败 Top 10（近 1 小时）
        </h2>
        {data.login_failure_top.length === 0 ? (
          <Notice tone="success" title="近 1 小时没有登录失败" />
        ) : (
          <div className="overflow-x-auto border border-divider">
            <table className="w-full min-w-[640px] border-collapse text-sm">
              <caption className="sr-only">近 1 小时登录失败次数最多的账号</caption>
              <thead>
                <tr className="bg-canvas text-left text-text-secondary">
                  <th scope="col" className="px-4 py-3 font-medium">账号</th>
                  <th scope="col" className="px-4 py-3 font-medium">失败次数</th>
                  <th scope="col" className="px-4 py-3 font-medium">最近失败时间</th>
                </tr>
              </thead>
              <tbody>
                {data.login_failure_top.map((item) => (
                  <tr key={item.account_id ?? item.account} className="border-t border-divider">
                    <td className="px-4 py-3 text-text-primary">{item.account}</td>
                    <td className="px-4 py-3 text-text-primary">{item.count}</td>
                    <td className="px-4 py-3 text-text-secondary">
                      {item.last_failed_at ? formatDateTime(item.last_failed_at) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section aria-labelledby="security-new-ip" className="space-y-3">
        <h2 id="security-new-ip" className="dn-display text-xl">
          新 IP 登录（近 24 小时）
        </h2>
        {data.new_ip_logins.length === 0 ? (
          <Notice tone="success" title="近 24 小时没有新 IP 登录" />
        ) : (
          <div className="overflow-x-auto border border-divider">
            <table className="w-full min-w-[640px] border-collapse text-sm">
              <caption className="sr-only">近 24 小时从账号历史未出现 IP 登录的记录</caption>
              <thead>
                <tr className="bg-canvas text-left text-text-secondary">
                  <th scope="col" className="px-4 py-3 font-medium">账号</th>
                  <th scope="col" className="px-4 py-3 font-medium">来源 IP</th>
                  <th scope="col" className="px-4 py-3 font-medium">首次出现时间</th>
                </tr>
              </thead>
              <tbody>
                {data.new_ip_logins.map((item) => (
                  <tr key={`${item.account_id}-${item.client_ip}`} className="border-t border-divider">
                    <td className="px-4 py-3 text-text-primary">{item.account}</td>
                    <td className="px-4 py-3 break-all text-text-secondary">{item.client_ip}</td>
                    <td className="px-4 py-3 text-text-secondary">{formatDateTime(item.first_seen_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <p className="flex items-center gap-2 text-xs text-text-secondary">
        <ShieldAlert className="h-4 w-4" aria-hidden="true" />
        只读聚合 · 生成于 {formatDateTime(data.generated_at)}
      </p>
    </div>
  );
}

function StatCard({ label, value, hint }: { label: string; value: number; hint: string }) {
  return (
    <div className="border border-divider bg-surface p-4">
      <span className="dn-kicker block text-text-secondary">{label}</span>
      <span className="dn-display mt-1 block text-2xl text-text-primary">{value}</span>
      <span className="mt-1 block text-xs text-text-secondary">{hint}</span>
    </div>
  );
}
