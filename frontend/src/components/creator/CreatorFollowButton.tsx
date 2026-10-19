'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bell, BellRing } from 'lucide-react';
import toast from 'react-hot-toast';

import { Button } from '@/components/ui/Button';
import { describeError } from '@/lib/api/client';
import { listFollows, removeFollow, setFollow } from '@/lib/api/interaction';
import { useAuthStore } from '@/stores/authStore';

/**
 * 贡献者关注按钮（§20.2）。关注创作者与收藏资料分开：
 * PUT 设置 / DELETE 取消，不做会反转状态的 toggle；初始状态来自关注列表查询。
 */
export function CreatorFollowButton({ creatorId }: { creatorId: string }) {
  const queryClient = useQueryClient();
  const { status, user } = useAuthStore();
  const authed = status === 'authenticated';
  const [pending, setPending] = useState(false);

  const followsQuery = useQuery({
    queryKey: ['my-follows', 'creator'],
    queryFn: () => listFollows({ target_type: 'creator' }),
    enabled: authed,
  });

  const following =
    followsQuery.data?.items.some((item) => item.target_id === creatorId) ?? false;

  const mutation = useMutation({
    mutationFn: () =>
      following ? removeFollow('creator', creatorId) : setFollow('creator', creatorId),
    onSuccess: () => {
      toast.success(following ? '已取消关注' : '已关注该贡献者');
      queryClient.invalidateQueries({ queryKey: ['my-follows'] });
      queryClient.invalidateQueries({ queryKey: ['follows'] });
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  async function toggle() {
    setPending(true);
    try {
      await mutation.mutateAsync();
    } finally {
      setPending(false);
    }
  }

  // 自己的主页不提供关注自己的入口。
  if (authed && user?.id === creatorId) return null;

  if (!authed) {
    return (
      <Link
        href="/login"
        className="inline-flex min-h-11 items-center gap-2 border border-control-border px-4 text-sm font-medium text-text-primary hover:bg-canvas"
      >
        <Bell className="h-4 w-4" aria-hidden="true" />
        登录后关注
      </Link>
    );
  }

  return (
    <Button variant="secondary" onClick={toggle} isLoading={pending}>
      {following ? (
        <BellRing className="h-4 w-4" aria-hidden="true" />
      ) : (
        <Bell className="h-4 w-4" aria-hidden="true" />
      )}
      {following ? '取消关注' : '关注贡献者'}
    </Button>
  );
}
