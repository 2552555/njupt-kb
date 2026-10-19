"use client";
import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Button } from "@/components/ui/Button";
import { Notice } from "@/components/common/Notice";
import { api, describeError } from "@/lib/api/client";
import type { CursorPage } from "@/lib/api/types";
import { hasPermission, useAuthStore } from "@/stores/authStore";
interface FailedEvent {
  id: string;
  event_type: string;
  status: string;
  attempts: number;
  created_at: string;
}
export default function Page() {
  const user = useAuthStore((s) => s.user);
  return hasPermission(user, 'job.retry') ? <Queue /> : <Notice title="需要管理员权限" />;
}
function Queue() {
  const user = useAuthStore((s) => s.user);

  const client = useQueryClient();
  const query = useInfiniteQuery({
    queryKey: ["admin-events", user?.id],
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }) =>
      (
        await api.get<CursorPage<FailedEvent>>("/admin/events", {
          params: { cursor: pageParam || undefined },
        })
      ).data,
    getNextPageParam: (p) => (p.has_more ? p.next_cursor : undefined),
    enabled: hasPermission(user, 'job.retry'),
  });
  const retry = useMutation({
    mutationFn: (item: FailedEvent) =>
      api.post(`/admin/events/${item.id}/retry`, {
        expected_attempts: item.attempts,
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["admin-events"] });
      toast.success("已重新排队，等待后台处理");
    },
    onError: (e) => {
      toast.error(describeError(e).message);
      client.invalidateQueries({ queryKey: ["admin-events"] });
    },
  });
  return (
    <div className="space-y-4">
      <h2 className="dn-display text-2xl">失败通知事件</h2>
      {query.error ? (
        <Notice
          tone="warning"
          title="事件列表加载失败"
          action={<Button onClick={() => query.refetch()}>重试</Button>}
        >
          {describeError(query.error).message}
        </Notice>
      ) : query.isPending ? (
        <p role="status">正在加载…</p>
      ) : query.data?.pages.every((p) => !p.items.length) ? (
        <Notice title="没有失败事件" />
      ) : (
        <ul className="space-y-3">
          {query.data?.pages
            .flatMap((p) => p.items)
            .map((item) => (
              <li
                key={item.id}
                className="flex flex-wrap items-center justify-between gap-3 border border-divider p-4"
              >
                <span className="break-all text-sm">
                  {item.event_type} · 已尝试 {item.attempts} 次
                </span>
                <Button
                  onClick={() => retry.mutate(item)}
                  isLoading={retry.isPending && retry.variables?.id === item.id}
                >
                  重新排队
                </Button>
              </li>
            ))}
        </ul>
      )}
      {query.hasNextPage && (
        <Button
          onClick={() => query.fetchNextPage()}
          isLoading={query.isFetchingNextPage}
        >
          更多事件
        </Button>
      )}
    </div>
  );
}
