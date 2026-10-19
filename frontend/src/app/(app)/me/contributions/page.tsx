'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { SkeletonList } from '@/components/ui/Skeleton';
import { describeError } from '@/lib/api/client';
import {
  CONTRIBUTION_KIND_LABELS,
  getMyCreatorProfile,
  listMyContributions,
  updateMyCreatorProfile,
  type ContributionKind,
  type CreatorSelfProfileOut,
} from '@/lib/api/creator';
import { formatDateTime } from '@/lib/utils';
import { useAuthStore } from '@/stores/authStore';
import { CreatorStats } from '@/components/workspace/PersonalOverview';

export default function MyContributionsPage() {
  const user = useAuthStore((s) => s.user);
  const profileQuery = useQuery({
    queryKey: ['my-creator-profile'],
    queryFn: getMyCreatorProfile,
    enabled: !!user,
  });

  return (
    <div className="space-y-8">
      <CreatorStats />
      <Notice tone="info" title="贡献与收益分开">
        贡献记录用于展示与运营，不承诺兑换现金。
      </Notice>

      <section aria-labelledby="contribution-summary">
        <h2 id="contribution-summary" className="dn-display text-2xl">
          贡献汇总
        </h2>
        <div className="mt-3">
          {profileQuery.isPending ? (
            <SkeletonList count={2} />
          ) : profileQuery.error ? (
            <Notice
              tone="warning"
              title="贡献汇总暂时无法加载"
              action={<Button variant="secondary" onClick={() => profileQuery.refetch()}>重试</Button>}
            >
              {describeError(profileQuery.error).message}
            </Notice>
          ) : (
            <SummaryBlock profile={profileQuery.data} />
          )}
        </div>
      </section>

      {profileQuery.data && (
        <ContributionProfileForm
          key={profileQuery.data.user_id}
          profile={profileQuery.data}
        />
      )}

      <ContributionList />
    </div>
  );
}

function SummaryBlock({ profile }: { profile: CreatorSelfProfileOut }) {
  const { contribution } = profile;
  const kinds = Object.entries(contribution.by_kind) as [ContributionKind, number][];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-text-primary">
        <span>贡献记录 {contribution.total} 次</span>
        <span>贡献分 {contribution.points}</span>
        <span>{contribution.is_creator ? '已建立贡献者资料' : '尚未建立贡献者资料'}</span>
      </div>
      {kinds.length > 0 ? (
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {kinds.map(([kind, count]) => (
            <div key={kind} className="border border-divider bg-surface p-4">
              <dt className="text-sm text-text-secondary">
                {CONTRIBUTION_KIND_LABELS[kind] ?? kind}
              </dt>
              <dd className="mt-1 text-2xl font-semibold text-text-primary">{count}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <Notice tone="info" title="还没有贡献记录">
          资料发布、纠错被采纳后会记为贡献。先
          <Link href="/upload" className="underline underline-offset-2">上传资料</Link>。
        </Notice>
      )}
      <p className="text-sm text-text-secondary">
        公开主页：<Link href={`/creators/${profile.user_id}`} className="underline underline-offset-2">
          查看他人看到的贡献者主页
        </Link>
      </p>
    </div>
  );
}

function ContributionProfileForm({ profile }: { profile: CreatorSelfProfileOut }) {
  const queryClient = useQueryClient();
  const [headline, setHeadline] = useState(profile.headline ?? '');
  const [bio, setBio] = useState(profile.bio ?? '');
  const [isPublic, setIsPublic] = useState(profile.is_public);

  const mutation = useMutation({
    mutationFn: () =>
      updateMyCreatorProfile({
        headline: headline.trim() || null,
        bio: bio.trim() || null,
        is_public: isPublic,
      }),
    onSuccess: () => {
      toast.success('贡献者资料已保存');
      queryClient.invalidateQueries({ queryKey: ['my-creator-profile'] });
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const inputClass = 'mt-1 min-h-11 w-full border border-control-border bg-surface px-3 py-2 text-sm';

  return (
    <section aria-labelledby="creator-profile-form">
      <h2 id="creator-profile-form" className="dn-display text-2xl">
        贡献者资料
      </h2>
      <p className="mt-2 max-w-3xl text-sm text-text-secondary">
        关闭公开后，他人只能看到你的昵称与贡献次数。
      </p>
      <form
        className="mt-3 max-w-2xl space-y-4 border border-divider bg-surface p-5"
        onSubmit={(event) => {
          event.preventDefault();
          mutation.mutate();
        }}
      >
        <fieldset disabled={mutation.isPending} className="space-y-4">
          <label className="block text-sm">
            一句话介绍
            <input
              value={headline}
              maxLength={200}
              onChange={(event) => setHeadline(event.target.value)}
              className={inputClass}
            />
          </label>
          <label className="block text-sm">
            简介
            <textarea
              value={bio}
              maxLength={2000}
              rows={4}
              onChange={(event) => setBio(event.target.value)}
              className={inputClass}
            />
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={isPublic}
              onChange={(event) => setIsPublic(event.target.checked)}
            />
            公开我的贡献者资料
          </label>
          <Button type="submit" isLoading={mutation.isPending}>
            保存贡献者资料
          </Button>
        </fieldset>
      </form>
    </section>
  );
}

function ContributionList() {
  const query = useInfiniteQuery({
    queryKey: ['my-contributions'],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => listMyContributions(pageParam),
    getNextPageParam: (page) => (page.has_more ? page.next_cursor : undefined),
  });
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <section aria-labelledby="contribution-list">
      <h2 id="contribution-list" className="dn-display text-2xl">
        贡献记录
      </h2>
      <div className="mt-3 space-y-3">
        {query.isPending ? (
          <SkeletonList count={3} />
        ) : query.error ? (
          <Notice
            tone="warning"
            title="贡献记录暂时无法加载"
            action={<Button variant="secondary" onClick={() => query.refetch()}>重试</Button>}
          >
            {describeError(query.error).message}
          </Notice>
        ) : items.length === 0 ? (
          <Notice tone="info" title="还没有贡献记录">
            </Notice>
        ) : (
          <ul className="space-y-3">
            {items.map((item) => (
              <li key={item.id} className="border border-divider bg-surface p-4">
                <p className="text-sm text-text-primary">
                  {CONTRIBUTION_KIND_LABELS[item.kind] ?? item.kind}
                  {item.revoked_at ? '（已撤销）' : ''}
                </p>
                {item.note && <p className="mt-1 text-sm text-text-secondary">{item.note}</p>}
                <p className="mt-1 text-xs text-text-secondary">
                  {item.points > 0 ? `+${item.points} 贡献分 · ` : ''}
                  {formatDateTime(item.created_at)}
                </p>
              </li>
            ))}
          </ul>
        )}
        {query.hasNextPage && (
          <Button
            variant="secondary"
            isLoading={query.isFetchingNextPage}
            onClick={() => query.fetchNextPage()}
          >
            加载更多
          </Button>
        )}
      </div>
    </section>
  );
}
