'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { SkeletonList } from '@/components/ui/Skeleton';
import { getCourse, listCourses, type CourseListItem, type CourseDetail } from '@/lib/api/catalog';
import { describeError } from '@/lib/api/client';
import {
  createRequest,
  followRequest,
  listRequests,
} from '@/lib/api/interaction';
import { useAuthStore } from '@/stores/authStore';

const statusLabels: Record<string, string> = {
  open: '寻找中',
  fulfilled: '已解决',
  closed: '已关闭',
};

const statusTones: Record<string, string> = {
  open: 'bg-amber text-on-color',
  fulfilled: 'bg-emerald text-on-color',
  closed: 'bg-steel text-on-color',
};

type RequestSort = 'latest' | 'hot';

export function RequestsClient({ initialCourse, initialOpen, initialSort = 'latest', initialKeyword = '' }: {
  initialCourse: CourseDetail | null;
  initialOpen: boolean;
  /** 初始排序：URL ?sort=hot 带入；切换后由组件状态维护。 */
  initialSort?: RequestSort;
  initialKeyword?: string;
}) {
  const queryClient = useQueryClient();
  const { status: sessionStatus, user } = useAuthStore();
  const authed = sessionStatus === 'authenticated';

  const [statusFilter, setStatusFilter] = useState<'open' | 'all'>('open');
  const [sort, setSort] = useState<RequestSort>(initialSort);
  const [formOpen, setFormOpen] = useState(initialOpen);
  const [followed, setFollowed] = useState<Record<string, boolean>>({});

  const listQuery = useInfiniteQuery({
    initialPageParam: null as string | null,
    queryKey: ['requests', statusFilter, sort, initialCourse?.id, user?.id],
    queryFn: ({ pageParam }) =>
      listRequests({ course_id: initialCourse?.id, cursor: pageParam, sort, status: statusFilter === 'all' ? undefined : 'open', limit: 20 }),
    getNextPageParam: page => page.has_more ? page.next_cursor : undefined,
  });

  const items = listQuery.data?.pages.flatMap(page => page.items) ?? [];
  const courseNamesQuery = useQuery({
    queryKey: ['request-course-names', items.map((item) => item.course_id).join(',')],
    enabled: items.length > 0,
    queryFn: async () => {
      const unique = [...new Set(items.map((item) => item.course_id))];
      const entries = await Promise.all(
        unique.map(async (id) => [id, (await getCourse(id)).name] as const),
      );
      return Object.fromEntries(entries) as Record<string, string>;
    },
  });

  const followMutation = useMutation({
    mutationFn: (requestId: string) => followRequest(requestId),
    onSuccess: (result, requestId) => {
      setFollowed((prev) => ({ ...prev, [requestId]: true }));
      queryClient.invalidateQueries({ queryKey: ['requests'] });
      toast.success(`已关注该需求，当前 ${result.follower_count} 人关注`);
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  return (
    <div className="space-y-6">
      <header className="bg-amber px-6 py-8 text-on-color">
        <p className="dn-kicker">RESOURCE REQUESTS</p>
        <h1 className="dn-display mt-2 text-3xl">求资料</h1>
      </header>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          {(
            [
              ['open', '寻找中'],
              ['all', '全部'],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={statusFilter === value}
              onClick={() => setStatusFilter(value)}
              className={`inline-flex min-h-11 items-center px-3 text-sm font-medium ${
                statusFilter === value
                  ? 'bg-brand text-on-color'
                  : 'border border-control-border text-text-primary hover:bg-canvas'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2" role="group" aria-label="排序方式">
          {(
            [
              ['latest', '最新'],
              ['hot', '最热'],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={sort === value}
              onClick={() => setSort(value)}
              className={`inline-flex min-h-11 items-center px-3 text-sm font-medium ${
                sort === value
                  ? 'bg-brand text-on-color'
                  : 'border border-control-border text-text-primary hover:bg-canvas'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <Button variant="primary" onClick={() => setFormOpen((open) => !open)}>
          {formOpen ? '收起' : '提出需求'}
        </Button>
      </div>

      {formOpen && (
        <RequestForm
          initialCourse={initialCourse}
          initialKeyword={initialKeyword}
          onDone={() => {
            setFormOpen(false);
            queryClient.invalidateQueries({ queryKey: ['requests'] });
          }}
        />
      )}

      {listQuery.isLoading ? (
        <SkeletonList count={3} />
      ) : listQuery.error ? (
        <Notice
          tone="warning"
          title="需求列表暂时无法加载"
          action={<Button variant="secondary" onClick={() => listQuery.refetch()}>重试</Button>}
        >
          {describeError(listQuery.error).message}
        </Notice>
      ) : items.length > 0 ? (
        <><ul className="space-y-3">
          {items.map((item) => (
            <li key={item.id} className="border border-divider bg-surface p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="break-words text-sm text-text-primary">{item.description}</p><Link href={`/requests/${item.id}`} className="mt-2 inline-flex min-h-11 items-center text-sm text-brand underline">查看与处理需求</Link>
                  <p className="mt-1 text-xs text-text-secondary">
                    {courseNamesQuery.data?.[item.course_id] ? (
                      <Link
                        href={`/courses/${item.course_id}`}
                        className="underline underline-offset-2 hover:text-text-primary"
                      >
                        {courseNamesQuery.data[item.course_id]}
                      </Link>
                    ) : (
                      <Link
                        href={`/courses/${item.course_id}`}
                        className="underline underline-offset-2 hover:text-text-primary"
                      >
                        课程 {item.course_id.slice(0, 8)}…
                      </Link>
                    )}
                    {item.scope ? ` · 范围：${item.scope}` : ''}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-3">
                  <span
                    className={`px-2 py-0.5 text-xs font-semibold ${
                      statusTones[item.status] ?? 'bg-steel text-on-color'
                    }`}
                  >
                    {statusLabels[item.status] ?? item.status}
                  </span>
                  {item.status === 'fulfilled' && !item.resolved_resource_accessible && (
                    <span className="px-2 py-0.5 text-xs font-semibold bg-amber text-on-color">
                      原解决资料当前不可访问
                    </span>
                  )}
                  <span className="text-xs text-text-secondary">
                    {item.follower_count} 人关注
                  </span>
                  {item.status === 'open' &&
                    (authed ? (
                      <Button
                        variant="secondary"
                        disabled={followed[item.id]}
                        isLoading={followMutation.isPending && followMutation.variables === item.id}
                        onClick={() => followMutation.mutate(item.id)}
                      >
                        {followed[item.id] ? '已关注' : '关注'}
                      </Button>
                    ) : (
                      <Link
                        href={`/login?next=${encodeURIComponent(initialCourse ? `/requests?course_id=${initialCourse.id}` : "/requests")}`}
                        className="inline-flex min-h-11 items-center border border-control-border px-3 text-sm text-text-primary hover:bg-canvas"
                      >
                        登录后关注
                      </Link>
                    ))}
                </div>
              </div>
            </li>
          ))}
        </ul>{listQuery.hasNextPage && <Button variant="secondary" onClick={()=>listQuery.fetchNextPage()} isLoading={listQuery.isFetchingNextPage}>更多需求</Button>}</>
      ) : (
        <EmptyState
          title={statusFilter === 'open' ? '当前没有进行中的需求' : '还没有资料需求'}
          description={
            sort === 'hot'
              ? '最热榜仅统计近 7 天创建的开放需求，可切换到「最新」查看全部。'
              : statusFilter === 'open'
              ? '可先浏览课程寻找现有资料。'
              : '切换到「寻找中」查看进行中的需求。'
          }
        />
      )}
    </div>
  );
}

function RequestForm({ onDone, initialCourse, initialKeyword }: { onDone: () => void; initialCourse: CourseDetail | null; initialKeyword: string }) {
  const authed = useAuthStore((state) => state.status === 'authenticated');
  const loginParams = new URLSearchParams({ new: '1' });
  if (initialCourse) loginParams.set('course_id', initialCourse.id);
  if (initialKeyword) loginParams.set('keyword', initialKeyword);
  const loginNext = `/requests?${loginParams}`;
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<Pick<CourseListItem, 'id' | 'name' | 'code' | 'college_name'> | null>(initialCourse);
  const [scope, setScope] = useState('');
  const [description, setDescription] = useState(initialKeyword);

  const courseSearch = useQuery({
    queryKey: ['request-course-search', query],
    queryFn: () => listCourses({ q: query.trim(), limit: 10 }),
    enabled: query.trim().length > 0,
  });

  const createMutation = useMutation({
    mutationFn: () =>
      createRequest({
        course_id: selected!.id,
        scope: scope.trim() || undefined,
        description: description.trim(),
      }),
    onSuccess: (result) => {
      if (result.created) {
        toast.success('需求已发布');
      } else {
        toast.success('已存在相同需求，已为你合并关注');
      }
      onDone();
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const courses = courseSearch.data?.items ?? [];
  const canSubmit = !!selected && description.trim().length > 0;

  if (!authed) return (
    <Notice title="登录后提出资料需求">
      <p>{initialCourse ? `已带入课程：${initialCourse.name}。` : '登录后选择课程，说明你需要的资料。'}</p>
      <Link href={`/login?next=${encodeURIComponent(loginNext)}`} className="mt-2 inline-flex min-h-11 items-center bg-brand px-4 text-sm text-on-color">登录并继续</Link>
    </Notice>
  );

  return (
    <form
      className="space-y-4 border border-divider bg-surface p-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (authed && canSubmit) createMutation.mutate();
      }}
    >
      <div>
        <label
          htmlFor="request-course"
          className="mb-1 block text-sm font-medium text-text-primary"
        >
          课程
        </label>
        <input
          id="request-course"
          value={selected ? `${selected.name}${selected.code ? `（${selected.code}）` : ''}` : query}
          readOnly={!!selected}
          onChange={(event) => {
            setSelected(null);
            setQuery(event.target.value);
          }}
          placeholder="输入课程名或代码搜索"
          className="min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
        />
        {selected && <Button type="button" variant="ghost" onClick={() => setSelected(null)}>更换课程</Button>}
        {!selected && courses.length > 0 && (
          <ul className="mt-2 border border-divider">
            {courses.map((course) => (
              <li key={course.id}>
                <button
                  type="button"
                  onClick={() => setSelected(course)}
                  className="flex min-h-11 w-full items-center px-3 text-left text-sm text-text-primary hover:bg-canvas"
                >
                  {course.name}
                  {course.code ? `（${course.code}）` : ''}
                  {course.college_name ? ` · ${course.college_name}` : ''}
                </button>
              </li>
            ))}
          </ul>
        )}
        {!selected && courseSearch.isFetching && (
          <p className="mt-2 text-xs text-text-secondary">搜索中…</p>
        )}
        {!selected && courseSearch.error && (
          <Notice tone="warning" title="课程搜索失败" action={<Button type="button" variant="secondary" onClick={() => courseSearch.refetch()}>重试</Button>}>
            {describeError(courseSearch.error).message}
          </Notice>
        )}
        {!selected && query.trim() && !courseSearch.error && !courseSearch.isFetching && courses.length === 0 && (
          <p className="mt-2 text-xs text-text-secondary">没有匹配的课程，请换个关键词。</p>
        )}
      </div>

      <label className="block text-sm font-medium text-text-primary">
        范围（可选）
        <input
          value={scope}
          onChange={(event) => setScope(event.target.value)}
          maxLength={200}
          placeholder="例如：2024 级、期末复习、含答案"
          className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
        />
      </label>

      <label className="block text-sm font-medium text-text-primary">
        需求描述
        <textarea
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          rows={3}
          maxLength={2000}
          placeholder="说明需要什么资料、用途与期望形式。"
          className="mt-1 w-full border border-control-border bg-surface px-3 py-2 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
        />
      </label>

      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onDone}>
          取消
        </Button>
        <Button type="submit" isLoading={createMutation.isPending} disabled={!canSubmit}>发布需求</Button>
      </div>
    </form>
  );
}
