"use client";
import Link from "next/link";
import { useCallback, useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Modal } from "@/components/ui/Modal";
import { Notice } from "@/components/common/Notice";
import { api, describeError } from "@/lib/api/client";
import {
  getRequest,
  changeRequestStatus,
  reopenRequest,
  listRequestResponses,
  submitRequestResponse,
} from "@/lib/api/interaction";
import type { ResourceListOut } from "@/lib/api/content";
import { isAdmin, useAuthStore } from "@/stores/authStore";

const labels: Record<string, string> = {
  open: "寻找中",
  fulfilled: "已解决",
  closed: "已关闭",
};
export function RequestWorkspace({ requestId }: { requestId: string }) {
  const user = useAuthStore((s) => s.user);
  const client = useQueryClient();
  const [keyword, setKeyword] = useState("");
  const [selected, setSelected] = useState("");
  const [reason, setReason] = useState("");
  const [action, setAction] = useState<{
    status: "closed" | "fulfilled";
    revision: number;
    resourceId?: string;
  } | null>(null);
  const [reopenAction, setReopenAction] = useState<{ revision: number } | null>(null);
  const [reopenReason, setReopenReason] = useState("");
  const query = useQuery({
    queryKey: ["request-detail", requestId, user?.id],
    queryFn: () => getRequest(requestId),
  });
  const responses = useInfiniteQuery({
    queryKey: ["request-responses", requestId],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => listRequestResponses(requestId, pageParam),
    getNextPageParam: (p) => (p.has_more ? p.next_cursor : undefined),
  });
  const choices = useInfiniteQuery({
    queryKey: ["request-choices", requestId, keyword, user?.id],
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }) =>
      (
        await api.get<ResourceListOut>(`/requests/${requestId}/resources`, {
          params: {
            keyword: keyword || undefined,
            cursor: pageParam || undefined,
          },
        })
      ).data,
    getNextPageParam: (p) => (p.has_more ? p.next_cursor : undefined),
    enabled: !!user && query.data?.status === "open",
  });
  function refresh() {
    client.invalidateQueries({ queryKey: ["request-detail", requestId] });
    client.invalidateQueries({ queryKey: ["request-responses", requestId] });
    client.invalidateQueries({ queryKey: ["requests"] });
    client.invalidateQueries({ queryKey: ["my-requests"] });
  }
  const mutation = useMutation({
    mutationFn: () => {
      if (!action) throw new Error("请选择操作");
      return changeRequestStatus(requestId, {
        status: action.status,
        expected_revision: action.revision,
        resource_id: action.resourceId,
        reason: reason.trim() || undefined,
      });
    },
    onSuccess: (updated) => {
      client.setQueryData(["request-detail", requestId, user?.id], updated);
      setAction(null);
      setReason("");
      refresh();
      toast.success("需求已更新");
    },
    onError: (error) => {
      const e = describeError(error);
      toast.error(e.message);
      if (e.code === "STALE_REVISION") {
        setAction(null);
        setReason("");
      }
      refresh();
    },
  });
  const response = useMutation({
    mutationFn: () => submitRequestResponse(requestId, selected),
    onSuccess: () => {
      setSelected("");
      refresh();
      toast.success("候选资料已提交，等待确认");
    },
    onError: (e) => {
      toast.error(describeError(e).message);
      refresh();
    },
  });
  const reopenMutation = useMutation({
    mutationFn: () => {
      if (!reopenAction) throw new Error("请先打开复核重开");
      return reopenRequest(requestId, {
        reason: reopenReason.trim(),
        expected_revision: reopenAction.revision,
      });
    },
    onSuccess: (updated) => {
      client.setQueryData(["request-detail", requestId, user?.id], updated);
      setReopenAction(null);
      setReopenReason("");
      refresh();
      toast.success("需求已重新打开，可继续寻找资料");
    },
    onError: (error) => {
      const e = describeError(error);
      toast.error(e.message);
      if (e.code === "STALE_REVISION") {
        // 版本冲突：关闭弹窗并刷新，让用户基于最新修订重试。
        setReopenAction(null);
        setReopenReason("");
      }
      refresh();
    },
  });
  const close = useCallback(() => {
    if (!mutation.isPending) setAction(null);
  }, [mutation.isPending]);
  const closeReopen = useCallback(() => {
    if (!reopenMutation.isPending) {
      setReopenAction(null);
      setReopenReason("");
    }
  }, [reopenMutation.isPending]);
  const row = query.data;
  if (query.isPending) return <p role="status">正在加载需求…</p>;
  if (!row || query.error)
    return (
      <Notice
        tone="warning"
        title="需求暂时无法加载"
        action={<Button onClick={() => query.refetch()}>重试</Button>}
      >
        {query.error ? describeError(query.error).message : "请稍后重试。"}
      </Notice>
    );
  const active = user?.status === "active";
  const manager = active && (user.id === row.requester_id || isAdmin(user));
  function open(status: "closed" | "fulfilled", resourceId?: string) {
    setAction({ status, resourceId, revision: row!.revision });
    setReason("");
  }
  return (
    <div className="space-y-6">
      <header className="border-b border-divider pb-5">
        <Link
          href={`/courses/${row.course_id}`}
          className="text-brand underline"
        >
          查看课程
        </Link>
        <h1 className="dn-display mt-3 text-3xl">资料需求</h1>
        <p className="mt-3 whitespace-pre-line break-words">
          {row.description}
        </p>
        <p className="mt-2 text-sm text-text-secondary">
          {labels[row.status]} · {row.follower_count} 人关注
          {row.scope ? ` · ${row.scope}` : ""}
        </p>
      </header>
      {row.status === "fulfilled" &&
        (row.resolved_resource_accessible && row.resolved_resource_id ? (
          <Notice title="需求已确认解决">
            <Link
              className="text-brand underline"
              href={`/resources/${row.resolved_resource_id}`}
            >
              {row.resolved_resource_title}
            </Link>
            <p>预览和原件下载仍按资料权限判断。</p>
          </Notice>
        ) : (
          <Notice tone="warning" title="原解决资料当前不可访问">
            保留已处理记录，可由发起者或管理员复核后重新打开。
          </Notice>
        ))}
      {manager && (
        <div className="flex flex-wrap gap-3">
          {row.status === "open" && (
            <Button variant="secondary" onClick={() => open("closed")}>
              关闭需求
            </Button>
          )}
          {(row.status === "fulfilled" || (row.status === "closed" && isAdmin(user))) && (
            <Button
              variant="secondary"
              onClick={() => {
                setReopenReason("");
                setReopenAction({ revision: row.revision });
              }}
            >
              复核重开
            </Button>
          )}
        </div>
      )}
      {row.status === "open" && (
        <section className="space-y-3" aria-label="回应需求">
          <h2 className="dn-display text-2xl">提交候选资料</h2>
          {active ? (
            <>
              <label className="block text-sm">
                查找本课程资料
                <input
                  value={keyword}
                  onChange={(e) => {
                    setKeyword(e.target.value);
                    setSelected("");
                  }}
                  className="mt-1 block min-h-11 w-full border border-control-border bg-surface px-3"
                />
              </label>
              <label className="block text-sm">
                选择资料
                <select
                  value={selected}
                  onChange={(e) => setSelected(e.target.value)}
                  className="mt-1 block min-h-11 w-full border border-control-border bg-surface px-3"
                >
                  <option value="">请选择</option>
                  {choices.data?.pages
                    .flatMap((p) => p.items)
                    .map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.title}
                      </option>
                    ))}
                </select>
              </label>
              {choices.error && (
                <Notice
                  tone="warning"
                  title="资料列表加载失败"
                  action={
                    <Button onClick={() => choices.refetch()}>重试</Button>
                  }
                >
                  {describeError(choices.error).message}
                </Notice>
              )}
              {choices.hasNextPage && (
                <Button
                  variant="ghost"
                  onClick={() => choices.fetchNextPage()}
                  isLoading={choices.isFetchingNextPage}
                >
                  更多资料
                </Button>
              )}
              <div className="flex flex-wrap gap-3">
                <Button
                  disabled={!selected}
                  isLoading={response.isPending}
                  onClick={() => response.mutate()}
                >
                  提交候选
                </Button>
                <Link
                  className="inline-flex min-h-11 items-center text-brand underline"
                  href={`/upload?course_id=${row.course_id}&request_id=${row.id}`}
                >
                  上传资料回应
                </Link>
              </div>
            </>
          ) : (
            <Link
              className="text-brand underline"
              href={`/login?next=${encodeURIComponent(`/requests/${requestId}`)}`}
            >
              登录后回应需求
            </Link>
          )}
        </section>
      )}
      <section aria-label="候选资料">
        <h2 className="dn-display text-2xl">候选回应</h2>
        {responses.error ? (
          <Notice
            tone="warning"
            title="候选回应加载失败"
            action={<Button onClick={() => responses.refetch()}>重试</Button>}
          >
            {describeError(responses.error).message}
          </Notice>
        ) : responses.isPending ? (
          <p role="status">正在加载回应…</p>
      ) : responses.data?.pages.every((p) => p.items.length === 0) ? (
        <EmptyState
          title="还没有候选资料"
          description="可以在上方搜索并提交本课程的资料作为候选。"
        />
      ) : (
          <ul className="mt-3 space-y-3">
            {responses.data?.pages
              .flatMap((p) => p.items)
              .map((item) => (
                <li
                  key={item.id}
                  className="flex flex-wrap items-center justify-between gap-3 border border-divider p-4"
                >
                  {item.available && item.resource_id ? (
                    <Link
                      className="text-brand underline"
                      href={`/resources/${item.resource_id}`}
                    >
                      {item.resource_title}
                    </Link>
                  ) : (
                    <span>候选资料当前不可访问</span>
                  )}
                  {manager &&
                    row.status === "open" &&
                    item.available &&
                    item.resource_id && (
                      <Button
                        onClick={() => open("fulfilled", item.resource_id!)}
                      >
                        确认解决
                      </Button>
                    )}
                </li>
              ))}
          </ul>
        )}
        {responses.hasNextPage && (
          <Button
            variant="secondary"
            onClick={() => responses.fetchNextPage()}
            isLoading={responses.isFetchingNextPage}
          >
            更多回应
          </Button>
        )}
      </section>
      <Modal
        isOpen={!!action}
        onClose={close}
        title={action?.status === "fulfilled" ? "确认解决" : "关闭需求"}
      >
        <div className="space-y-4 p-6">
          <label className="block text-sm">
            处理理由{action?.status === "fulfilled" ? "（可选）" : ""}
            <textarea
              rows={3}
              maxLength={500}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              className="mt-2 block w-full border border-control-border bg-surface p-2"
            />
          </label>
          <div className="flex gap-3">
            <Button
              onClick={() => mutation.mutate()}
              isLoading={mutation.isPending}
              disabled={action?.status !== "fulfilled" && !reason.trim()}
            >
              确认处理
            </Button>
            <Button
              variant="secondary"
              onClick={close}
              disabled={mutation.isPending}
            >
              取消
            </Button>
          </div>
        </div>
      </Modal>
      <Modal
        isOpen={!!reopenAction}
        onClose={closeReopen}
        title="复核重开需求"
      >
        <div className="space-y-4 p-6">
          <p className="text-sm text-text-secondary">
            需求将回到「寻找中」并清空原解决资料，可重新收集候选资料。请填写复核理由。
          </p>
          <label className="block text-sm">
            复核理由（必填）
            <textarea
              rows={3}
              maxLength={500}
              value={reopenReason}
              onChange={(e) => setReopenReason(e.target.value)}
              className="mt-2 block w-full border border-control-border bg-surface p-2"
            />
          </label>
          <div className="flex gap-3">
            <Button
              onClick={() => reopenMutation.mutate()}
              isLoading={reopenMutation.isPending}
              disabled={!reopenReason.trim()}
            >
              确认重开
            </Button>
            <Button
              variant="secondary"
              onClick={closeReopen}
              disabled={reopenMutation.isPending}
            >
              取消
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
