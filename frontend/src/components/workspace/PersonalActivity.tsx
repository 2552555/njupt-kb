'use client';
import Link from 'next/link';
import { Button } from '@/components/ui/Button';
import { formatDateTime } from '@/lib/utils';
import type { DownloadHistoryItem, FeedItem } from '@/lib/api/workspaces';
import { QueryFeedback, useWorkspaceList } from './Workspace';

export function PersonalActivity({ kind }: { kind: 'feed' | 'downloads' }) {
  const query = useWorkspaceList<DownloadHistoryItem | FeedItem>(`my-${kind}`, `/me/${kind}`);
  const items = query.data?.pages.flatMap(page => page.items) ?? [];
  return <section className="space-y-4">
    <header className="flex flex-wrap justify-between gap-3"><h2 className="dn-display text-2xl">{kind === 'feed' ? '关注动态' : '下载历史'}</h2>{kind === 'feed' && <Link href="/me/follows" className="text-brand underline">管理关注</Link>}</header>
    {kind === 'downloads' && <p className="text-sm text-text-secondary">记录下载授权；重新下载仍需通过当前权限与配额校验。</p>}
    <QueryFeedback pending={query.isPending} error={query.error} empty={!items.length} retry={() => query.refetch()} emptyText={kind === 'feed' ? '暂无动态，可先关注课程或创作者' : '暂无下载记录'} />
    <ul className="space-y-3">{items.map(item => <li key={item.id} className="border border-divider bg-surface p-5">{'available' in item ? <>
      {item.available && item.resource_id ? <Link href={`/resources/${item.resource_id}`} className="text-lg font-semibold text-brand">{item.title}</Link> : <h3 className="font-semibold">原资料当前不可访问</h3>}
      <p className="mt-2 break-all text-sm text-text-secondary">{item.file_name}{item.version_no ? ` · V${item.version_no}` : ''} · {formatDateTime(item.created_at)}</p>
      <p className="mt-1 text-xs text-text-secondary">授权状态：{({ issued: '已签发', consumed: '已使用', expired: '已到期', revoked: '已撤销' } as Record<string, string>)[item.status] ?? item.status}</p>
    </> : <>
      <Link href={`/resources/${item.id}`} className="text-lg font-semibold text-brand">{item.title}</Link>
      <p className="mt-2 text-sm text-text-secondary"><Link href={`/courses/${item.course_id}`} className="underline">{item.course_name}</Link> · V{item.version_no} · {formatDateTime(item.updated_at)}</p>
      <Link href={`/creators/${item.owner_id}`} className="mt-2 inline-block text-sm text-brand underline">查看贡献者</Link>
    </>}</li>)}</ul>
    {query.hasNextPage && <Button variant="secondary" onClick={() => query.fetchNextPage()} isLoading={query.isFetchingNextPage}>加载更多</Button>}
  </section>;
}
