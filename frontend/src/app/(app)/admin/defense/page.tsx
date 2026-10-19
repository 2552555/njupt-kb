'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { SkeletonList } from '@/components/ui/Skeleton';
import { describeError } from '@/lib/api/client';
import {
  createHoneytoken,
  defenseEvents,
  defenseHoneytokens,
  defenseOverview,
  defensePolicies,
  defenseSubjects,
  retireHoneytoken,
  revokeRiskSubject,
} from '@/lib/api/defense';
import { formatDateTime } from '@/lib/utils';

const LEVEL_LABEL: Record<string, string> = {
  normal: '正常',
  suspicious: '可疑',
  high: '高风险',
};

const ACTION_LABEL: Record<string, string> = {
  observe: '观察',
  throttle: '收紧限流',
  challenge: '二次验证',
  review: '人工复核',
};

const KIND_LABEL: Record<string, string> = {
  honeypot_hit: '蜜罐命中',
  honeytoken_hit: '标记触发',
  enumeration: '路径枚举',
  boundary_probe: '边界探测',
  param_sweep: '参数扫描',
  deep_pagination: '深分页',
  rate_pressure: '速率压力',
};

export default function AdminDefensePage() {
  const queryClient = useQueryClient();
  const [label, setLabel] = useState('');
  const [placement, setPlacement] = useState('test_fixture');
  const [rawToken, setRawToken] = useState<string | null>(null);

  const overview = useQuery({ queryKey: ['defense-overview'], queryFn: defenseOverview });
  const subjects = useQuery({ queryKey: ['defense-subjects'], queryFn: () => defenseSubjects() });
  const events = useQuery({ queryKey: ['defense-events'], queryFn: () => defenseEvents() });
  const tokens = useQuery({ queryKey: ['defense-tokens'], queryFn: defenseHoneytokens });
  const policies = useQuery({ queryKey: ['defense-policies'], queryFn: defensePolicies });

  function invalidateAll() {
    for (const key of ['defense-overview', 'defense-subjects', 'defense-events', 'defense-tokens', 'defense-policies']) {
      queryClient.invalidateQueries({ queryKey: [key] });
    }
  }

  const revokeMutation = useMutation({
    mutationFn: (id: string) => {
      const reason = window.prompt('撤销理由（用于复盘，至少 2 个字符）', '误判：校内安全扫描');
      if (!reason || reason.trim().length < 2) throw new Error('需要填写撤销理由');
      return revokeRiskSubject(id, reason.trim());
    },
    onSuccess: () => {
      toast.success('已撤销该主体的临时处置');
      invalidateAll();
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const createTokenMutation = useMutation({
    mutationFn: () =>
      createHoneytoken({
        label: label.trim(),
        placement,
      }),
    onSuccess: (result) => {
      setRawToken(result.raw_token);
      setLabel('');
      toast.success('标记已登记，明文只显示一次');
      invalidateAll();
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const retireTokenMutation = useMutation({
    mutationFn: (id: string) => {
      const reason = window.prompt('退役理由', '试点结束');
      if (!reason || reason.trim().length < 2) throw new Error('需要填写退役理由');
      return retireHoneytoken(id, reason.trim());
    },
    onSuccess: () => {
      toast.success('标记已退役');
      invalidateAll();
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const mode = overview.data?.mode ?? 'observe';

  return (
    <div className="space-y-6">
      <header className="border-b border-divider pb-6">
        <h1 className="dn-display text-3xl">主动防御</h1>
        <p className="mt-3 text-text-secondary">
          诱饵与风险评分只用于发现侦察行为并留痕。默认观察模式不改变正常请求结果；处置只临时收紧限流，不放宽固定配额。
        </p>
      </header>

      <Notice tone={mode === 'enforce' ? 'warning' : 'info'} title={`当前模式：${mode}`}>
        {mode === 'observe'
          ? '观察模式：只记录信号与评分，不改变任何业务响应。确认误伤率后再切换到 enforce。'
          : mode === 'enforce'
            ? '强制模式：达到阈值的来源会被临时收紧限流或转人工复核；处置到期自动恢复，可随时撤销。'
            : '防御已关闭：不记录信号，也不做任何处置。'}
      </Notice>

      {overview.data && (
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[
            { label: '近 24 小时信号', value: overview.data.events_recent },
            { label: '可疑主体', value: overview.data.subjects_suspicious },
            { label: '高风险主体', value: overview.data.subjects_high },
            { label: '生效标记', value: overview.data.honeytokens_active },
          ].map((item) => (
            <div key={item.label} className="border border-divider bg-surface p-4">
              <dt className="text-sm text-text-secondary">{item.label}</dt>
              <dd className="mt-1 text-2xl font-semibold">{item.value}</dd>
            </div>
          ))}
        </dl>
      )}

      <section aria-labelledby="risk-subjects" className="space-y-3">
        <h2 id="risk-subjects" className="text-lg font-semibold">风险主体</h2>
        {subjects.isLoading ? (
          <SkeletonList count={3} />
        ) : subjects.isError ? (
          <Notice tone="error" title="无法加载风险主体">{describeError(subjects.error).message}</Notice>
        ) : (subjects.data?.items.length ?? 0) === 0 ? (
          <EmptyState title="没有风险主体" description="信号累积达到阈值后会出现在这里。" />
        ) : (
          <ul className="divide-y divide-divider border border-divider bg-surface">
            {subjects.data!.items.map((item) => (
              <li key={item.id} className="flex flex-wrap items-start justify-between gap-3 p-4">
                <div className="min-w-0">
                  <p className="font-mono text-sm">{item.subject_key}</p>
                  <p className="mt-1 text-sm text-text-secondary">
                    {LEVEL_LABEL[item.level] ?? item.level} · 分数 {item.score.toFixed(1)} · 信号 {item.signal_count} 次 · 处置{' '}
                    {ACTION_LABEL[item.action] ?? item.action}
                  </p>
                  <p className="mt-1 text-xs text-text-secondary">
                    {item.last_signal_at ? `最近信号 ${formatDateTime(item.last_signal_at)}` : '无信号时间'}
                    {item.action_expires_at ? ` · 处置到期 ${formatDateTime(item.action_expires_at)}` : ''}
                    {item.revoked_at ? ' · 已撤销' : ''}
                  </p>
                </div>
                {!item.revoked_at && item.action !== 'observe' && (
                  <Button variant="secondary" size="sm" onClick={() => revokeMutation.mutate(item.id)}>
                    撤销处置
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="defense-events" className="space-y-3">
        <h2 id="defense-events" className="text-lg font-semibold">信号事件</h2>
        {events.isLoading ? (
          <SkeletonList count={3} />
        ) : (events.data?.items.length ?? 0) === 0 ? (
          <EmptyState title="没有信号事件" description="蜜罐命中、标记触发或枚举行为会记录在这里。" />
        ) : (
          <ul className="divide-y divide-divider border border-divider bg-surface">
            {events.data!.items.map((item) => (
              <li key={item.id} className="p-4">
                <p className="text-sm">
                  {KIND_LABEL[item.kind] ?? item.kind} · {item.signal} · 权重 {item.weight}
                </p>
                <p className="mt-1 font-mono text-xs text-text-secondary">
                  {item.subject_key} · {item.method ?? '-'} {item.path_template ?? '-'}
                </p>
                <p className="mt-1 text-xs text-text-secondary">
                  {formatDateTime(item.occurred_at)}
                  {item.decoy_label ? ` · 诱饵 ${item.decoy_label}` : ''}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="honeytokens" className="space-y-3">
        <h2 id="honeytokens" className="text-lg font-semibold">蜜罐标记</h2>
        <p className="text-sm text-text-secondary">
          标记没有任何业务权限，只能用于发现异常使用。请放在隔离测试材料中，不要放进真实资料或正常导航。
        </p>
        <div className="flex flex-wrap items-end gap-2 border border-divider bg-surface p-4">
          <div className="min-w-48 flex-1">
            <label htmlFor="token-label" className="block text-sm font-medium">标记名称</label>
            <input
              id="token-label"
              value={label}
              maxLength={64}
              onChange={(event) => setLabel(event.target.value)}
              className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3 text-sm"
              placeholder="例如：隔离样本 01"
            />
          </div>
          <div>
            <label htmlFor="token-placement" className="block text-sm font-medium">投放位置</label>
            <select
              id="token-placement"
              value={placement}
              onChange={(event) => setPlacement(event.target.value)}
              className="mt-1 min-h-11 border border-control-border bg-surface px-3 text-sm"
            >
              <option value="test_fixture">测试夹具</option>
              <option value="isolated_material">隔离材料</option>
              <option value="static_decoy">静态诱饵</option>
            </select>
          </div>
          <Button
            onClick={() => createTokenMutation.mutate()}
            isLoading={createTokenMutation.isPending}
            disabled={label.trim().length === 0}
          >
            登记标记
          </Button>
        </div>
        {rawToken && (
          <Notice tone="warning" title="明文只显示一次">
            <code className="break-all font-mono text-sm">{rawToken}</code>
            <p className="mt-2 text-sm">请立即放入隔离材料；关闭页面后无法再次读取。</p>
          </Notice>
        )}
        {(tokens.data?.items.length ?? 0) === 0 ? (
          <EmptyState title="还没有标记" description="登记后把明文放进隔离测试材料。" />
        ) : (
          <ul className="divide-y divide-divider border border-divider bg-surface">
            {tokens.data!.items.map((item) => (
              <li key={item.id} className="flex flex-wrap items-center justify-between gap-3 p-4">
                <div>
                  <p className="text-sm font-medium">{item.label}</p>
                  <p className="mt-1 text-xs text-text-secondary">
                    {item.placement} · {item.status === 'active' ? '生效' : '已退役'} · 创建 {formatDateTime(item.created_at)}
                  </p>
                </div>
                {item.status === 'active' && (
                  <Button variant="ghost" size="sm" onClick={() => retireTokenMutation.mutate(item.id)}>
                    退役
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="defense-policies" className="space-y-3">
        <h2 id="defense-policies" className="text-lg font-semibold">策略版本</h2>
        {policies.data && (
          <p className="text-sm text-text-secondary">
            当前生效：{policies.data.active.source} · 模式 {policies.data.active.mode} · 可疑阈值{' '}
            {policies.data.active.suspicious_score} · 高风险阈值 {policies.data.active.high_score} · 窗口{' '}
            {policies.data.active.window_seconds} 秒
          </p>
        )}
        {(policies.data?.items.length ?? 0) === 0 ? (
          <Notice tone="info" title="使用默认策略">
            尚未创建策略版本，当前阈值来自服务配置。需要调整时通过接口新增版本，历史版本会保留。
          </Notice>
        ) : (
          <ul className="divide-y divide-divider border border-divider bg-surface">
            {policies.data!.items.map((item) => (
              <li key={item.id} className="p-4 text-sm">
                第 {item.version} 版 · {item.mode} · 可疑 {item.suspicious_score} / 高风险 {item.high_score} · 窗口{' '}
                {item.window_seconds}s · 处置 {item.action_ttl_seconds}s
                {item.note ? ` · ${item.note}` : ''}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
