'use client';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { getPersonalOverview } from '@/lib/api/workspaces';
import { useAuthStore } from '@/stores/authStore';
import { QueryFeedback } from './Workspace';

export function CreatorStats() {
  const user = useAuthStore(s => s.user);
  const query = useQuery({ queryKey: ['personal-overview', user?.id], queryFn: getPersonalOverview, enabled: !!user });
  const data = query.data;
  return <section className="space-y-3" aria-label="创作数据">
    <h2 className="dn-display text-2xl">创作数据</h2>
    <QueryFeedback pending={query.isPending} error={query.error} empty={false} retry={() => query.refetch()} />
    {data && <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[
      ['已发布资料', data.published], ['下载授权次数', data.received_download_grants],
      ['收到评分', data.received_ratings], ['平均评分', data.average_rating?.toFixed(1) ?? '—'],
    ].map(([name, value]) => <div key={name} className="border border-divider bg-surface p-4"><dt className="text-sm text-text-secondary">{name}</dt><dd className="dn-display mt-2 text-3xl">{value}</dd></div>)}</dl>}
    <p className="text-xs text-text-secondary">下载授权次数表示已签发的许可，不代表文件已传输完成。</p>
  </section>;
}
export function PersonalOverview() {
  const user = useAuthStore(s => s.user);
  const query = useQuery({ queryKey: ['personal-overview', user?.id], queryFn: getPersonalOverview, enabled: !!user });
  const data = query.data;
  const tasks = data ? [
    { href: '/me/resources', label: '草稿', count: data.drafts },
    { href: '/me/resources', label: '待审核版本', count: data.pending_reviews },
    { href: '/me/notifications', label: '未读通知', count: data.unread_notifications },
    { href: '/me/resources', label: '求资料与纠错', count: data.open_requests + data.open_corrections },
  ] : [];
  return <div className="space-y-8">
    <header className="flex flex-wrap items-end justify-between gap-3"><h2 className="dn-display text-2xl">{user?.display_name}，欢迎回来</h2><Link href="/upload" className="bg-brand px-5 py-3 text-sm text-on-color">上传资料</Link></header>
    <QueryFeedback pending={query.isPending} error={query.error} empty={false} retry={() => query.refetch()} />
    {data && <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[
        ['/me/resources', '我的资料', data.resources], ['/me/favorites', '收藏', data.favorites],
        ['/me/follows', '关注', data.follows], ['/me/downloads', '下载记录', data.downloads],
      ].map(([href, label, count]) => <Link key={href} href={String(href)} className="border border-divider bg-surface p-5 hover:border-brand"><p className="text-sm text-text-secondary">{label}</p><p className="dn-display mt-2 text-3xl">{count}</p></Link>)}</div>
      <section className="space-y-3"><h3 className="dn-display text-xl">待办事项</h3><ul className="divide-y divide-divider border border-divider">{tasks.map(item => <li key={item.label}><Link href={item.href} className="flex min-h-14 items-center justify-between gap-4 px-4 hover:bg-canvas"><span>{item.label}</span><span className="font-semibold">{item.count}</span></Link></li>)}</ul></section>
      <div className="grid gap-4 md:grid-cols-2"><Link href="/me/feed" className="border border-divider p-5"><h3 className="text-lg font-semibold">关注动态 →</h3></Link><Link href="/me/downloads" className="border border-divider p-5"><h3 className="text-lg font-semibold">下载记录 →</h3></Link></div>
    </>}
    <CreatorStats />
  </div>;
}
