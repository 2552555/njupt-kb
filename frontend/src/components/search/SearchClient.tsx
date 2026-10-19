'use client';

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { ArrowRight, BookOpen, FileText, Search, UserRound } from 'lucide-react';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { SkeletonList } from '@/components/ui/Skeleton';
import { describeError } from '@/lib/api/client';
import {
  hotQueries,
  suggest,
  unifiedSearch,
  type HotResult,
  type SuggestItem,
} from '@/lib/api/search';
import { isResourceType, RESOURCE_TYPE_LABELS } from '@/lib/api/types';

const SUGGEST_LIST_ID = 'search-suggest-list';
const SUGGEST_DEBOUNCE_MS = 250;

/** 热词小块的颜色按顺序轮换，保持品牌色块而不引入新色。 */
const HOT_COLORS = [
  'bg-brand',
  'bg-cyan',
  'bg-violet',
  'bg-emerald',
  'bg-amber',
  'bg-orange',
  'bg-steel',
];

const SUGGEST_ICONS = {
  course: BookOpen,
  resource: FileText,
  creator: UserRound,
} as const;

const SUGGEST_TYPE_LABELS = {
  course: '课程',
  resource: '资料',
  creator: '创作者',
} as const;

function resourceTypeLabel(value: string): string {
  return isResourceType(value) ? RESOURCE_TYPE_LABELS[value] : '其他资料';
}

export function SearchClient() {
  const searchParams = useSearchParams();
  // 重复参数取首值：URLSearchParams.get 已按首值返回，与项目既有处理一致。
  const q = (searchParams.get('q') ?? '').trim();
  return <UnifiedSearch q={q} />;
}

function UnifiedSearch({ q }: { q: string }) {
  const router = useRouter();
  const [input, setInput] = useState(q);
  const [debounced, setDebounced] = useState(q);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  // URL 变化（前进/后退、点击热词）时同步输入框：在渲染期比较而不是在 effect 里 setState。
  const [lastQ, setLastQ] = useState(q);
  if (q !== lastQ) {
    setLastQ(q);
    setInput(q);
  }

  // 250ms 防抖：避免按键级请求；不足 1 字符不请求（清空后同样延时归零）。
  useEffect(() => {
    const keyword = input.trim();
    const timer = window.setTimeout(() => setDebounced(keyword), SUGGEST_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [input]);

  useEffect(() => {
    function onDocMouseDown(event: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(event.target as Node)) {
        setOpen(false);
        setActiveIndex(-1);
      }
    }
    document.addEventListener('mousedown', onDocMouseDown);
    return () => document.removeEventListener('mousedown', onDocMouseDown);
  }, []);

  const suggestQuery = useQuery({
    queryKey: ['search-suggest', debounced],
    queryFn: () => suggest(debounced),
    // 只在输入框聚焦且有关键词时请求：落地页有 q 但未聚焦时不发多余请求。
    enabled: open && debounced.length >= 1,
    staleTime: 60_000,
    retry: false,
  });

  const hotQuery = useQuery({
    queryKey: ['search-hot'],
    queryFn: hotQueries,
    staleTime: 5 * 60_000,
  });

  const results = useQuery({
    queryKey: ['search-unified', q],
    queryFn: () => unifiedSearch({ q, scope: 'all', limit: 10 }),
    enabled: q.length > 0,
  });

  const suggestions = suggestQuery.data?.items ?? [];
  const suggestionsVisible = open && input.trim().length >= 1 && suggestions.length > 0;
  // 建议列表可能因重新请求变短，读取时夹取，避免越界或高亮到不存在的项。
  const activeSuggestion = activeIndex >= 0 && activeIndex < suggestions.length ? suggestions[activeIndex] : null;

  function goTo(keyword: string) {
    const value = keyword.trim();
    setOpen(false);
    setActiveIndex(-1);
    setInput(value);
    router.push(value ? `/search?q=${encodeURIComponent(value)}` : '/search');
  }

  function chooseSuggestion(item: SuggestItem) {
    setOpen(false);
    setActiveIndex(-1);
    router.push(item.href);
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    // 输入法组合中的 Enter/方向键交给输入法处理，不触发跳转。
    if (event.nativeEvent.isComposing) return;

    if (event.key === 'Escape') {
      if (open) {
        event.preventDefault();
        setOpen(false);
        setActiveIndex(-1);
      }
      inputRef.current?.focus();
      return;
    }

    if (!suggestionsVisible) return;

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActiveIndex((index) => (index + 1) % suggestions.length);
      return;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActiveIndex((index) => (index <= 0 ? suggestions.length - 1 : index - 1));
      return;
    }
    if (event.key === 'Enter' && activeSuggestion) {
      event.preventDefault();
      chooseSuggestion(activeSuggestion);
    }
  }

  const data = results.data;
  const resourceHref = q ? `/search/resources?q=${encodeURIComponent(q)}` : '/search/resources';

  return (
    <div className="space-y-8">
      <header className="bg-cyan px-6 py-8 text-on-color">
        <p className="dn-kicker">SEARCH</p>
        <h1 className="dn-display mt-2 text-3xl">搜索课程、资料与创作者</h1>
        <p className="mt-2 max-w-2xl text-sm">
          一次查询同时覆盖课程名与课程代码、已发布资料标题、创作者显示名。
        </p>
      </header>

      <form
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          goTo(input);
        }}
        className="border border-divider bg-surface p-4"
      >
        <label htmlFor="search-q" className="mb-1 block text-sm font-medium text-text-primary">
          关键词
        </label>
        <div ref={boxRef} className="relative">
          <div className="flex">
            <input
              ref={inputRef}
              id="search-q"
              type="search"
              role="combobox"
              aria-expanded={suggestionsVisible}
              aria-controls={SUGGEST_LIST_ID}
              aria-autocomplete="list"
              aria-activedescendant={
                suggestionsVisible && activeSuggestion
                  ? `${SUGGEST_LIST_ID}-option-${activeIndex}`
                  : undefined
              }
              autoComplete="off"
              value={input}
              onChange={(event) => {
                setInput(event.target.value);
                setOpen(true);
              }}
              onFocus={() => setOpen(true)}
              onKeyDown={onKeyDown}
              placeholder="例如：模电、B0300011、数构期末、某位创作者"
              className="min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary placeholder:text-text-secondary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
            />
            <button
              type="submit"
              className="inline-flex min-h-11 shrink-0 items-center gap-2 bg-brand px-5 text-sm font-medium text-on-color hover:bg-brand/90"
            >
              <Search className="h-4 w-4" aria-hidden="true" />
              搜索
            </button>
          </div>

          {suggestionsVisible && (
            <div className="absolute left-0 right-0 top-full z-20 border border-divider bg-surface shadow-2">
              <ul id={SUGGEST_LIST_ID} role="listbox" aria-label="搜索建议">
                {suggestions.map((item, index) => {
                  const Icon = SUGGEST_ICONS[item.type];
                  const active = index === activeIndex;
                  return (
                    <li
                      key={`${item.type}-${item.id}`}
                      id={`${SUGGEST_LIST_ID}-option-${index}`}
                      role="option"
                      aria-selected={active}
                      onMouseDown={(event) => event.preventDefault()}
                      onMouseEnter={() => setActiveIndex(index)}
                      onClick={() => chooseSuggestion(item)}
                      className={`flex min-h-11 cursor-pointer items-center gap-3 border-b border-divider px-3 py-2 text-sm last:border-b-0 ${
                        active ? 'bg-canvas' : 'bg-surface'
                      }`}
                    >
                      <Icon className="h-4 w-4 shrink-0 text-text-secondary" aria-hidden="true" />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-text-primary">{item.label}</span>
                        {item.sublabel && (
                          <span className="block truncate text-xs text-text-secondary">
                            {item.sublabel}
                          </span>
                        )}
                      </span>
                      <span className="shrink-0 text-xs text-text-secondary">
                        {SUGGEST_TYPE_LABELS[item.type]}
                      </span>
                    </li>
                  );
                })}
              </ul>
              <p className="border-t border-divider px-3 py-2 text-xs text-text-secondary">
                ↑ ↓ 选择，Enter 打开，Esc 关闭
              </p>
            </div>
          )}

          {open && input.trim().length >= 1 && suggestQuery.isFetching && !suggestionsVisible && (
            <div className="absolute left-0 right-0 top-full z-20 border border-divider bg-surface px-3 py-3 text-sm text-text-secondary shadow-2">
              正在匹配建议…
            </div>
          )}
        </div>
        <p className="mt-2 text-xs text-text-secondary">
          输入即出建议；直接回车进入完整结果。
        </p>
      </form>

      {!q && <HotQueries query={hotQuery} onPick={goTo} />}

      {q && (
        <section aria-label={`「${q}」的搜索结果`} className="space-y-6">
          {results.isLoading ? (
            <SkeletonList count={3} />
          ) : results.error ? (
            <Notice
              tone="warning"
              title="搜索暂时无法完成"
              action={
                <Button variant="secondary" onClick={() => results.refetch()}>
                  重试
                </Button>
              }
            >
              {describeError(results.error).message}。这不表示没有匹配内容。
            </Notice>
          ) : !data ? (
            <Notice tone="info" title="暂无搜索结果" />
          ) : data.total === 0 ? (
            <Notice tone="info" title={`没有找到与「${q}」相关的内容`}>
              <p>可以换一个关键词，或到资料页按类型、年份继续筛选。</p>
              <div className="mt-2 flex flex-wrap gap-4">
                <Link href={resourceHref} className="inline-flex min-h-11 items-center underline">
                  在资料中继续筛选
                </Link>
                <Link
                  href={`/requests?new=1&keyword=${encodeURIComponent(q)}`}
                  className="inline-flex min-h-11 items-center underline"
                >
                  求资料
                </Link>
              </div>
            </Notice>
          ) : (
            <>
              <p className="text-sm text-text-secondary">
                「{q}」共 {data.total} 条结果（课程 {data.courses.length} · 资料{' '}
                {data.resources.length} · 创作者 {data.creators.length}）
              </p>

              <CourseGroup courses={data.courses} />
              <ResourceGroup resources={data.resources} resourceHref={resourceHref} />
              <CreatorGroup creators={data.creators} />

              <p className="flex flex-wrap items-center gap-3 border-t border-divider pt-4 text-sm">
                <Link
                  href={resourceHref}
                  className="inline-flex min-h-11 items-center gap-2 text-brand underline"
                >
                  在资料中继续按类型与年份筛选
                  <ArrowRight className="h-4 w-4" aria-hidden="true" />
                </Link>
                <Link
                  href={`/requests?new=1&keyword=${encodeURIComponent(q)}`}
                  className="inline-flex min-h-11 items-center text-text-secondary underline"
                >
                  求资料
                </Link>
              </p>
            </>
          )}
        </section>
      )}
    </div>
  );
}

function HotQueries({
  query,
  onPick,
}: {
  query: UseQueryResult<HotResult>;
  onPick: (keyword: string) => void;
}) {
  return (
    <section aria-labelledby="search-hot" className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="search-hot" className="dn-display text-xl">
          热门搜索
        </h2>
        {query.data && (
          <p className="text-xs text-text-secondary">
            近 {query.data.window_days} 天被搜索最多的关键词
          </p>
        )}
      </div>

      {query.isLoading ? (
        <div className="flex flex-wrap gap-2" role="status" aria-label="热词加载中">
          {[0, 1, 2, 3, 4].map((index) => (
            <span key={index} className="dn-skeleton h-11 w-28" />
          ))}
        </div>
      ) : query.isError ? (
        <p className="text-sm text-text-secondary">热词暂时不可用，直接输入关键词即可搜索。</p>
      ) : !query.data || query.data.items.length === 0 ? (
        <p className="text-sm text-text-secondary">
          近 {query.data?.window_days ?? 7} 天还没有足够的热词，直接输入关键词即可搜索。
        </p>
      ) : (
        <ul className="flex flex-wrap gap-2">
          {query.data.items.map((item, index) => (
            <li key={item.query}>
              <button
                type="button"
                onClick={() => onPick(item.query)}
                className={`inline-flex min-h-11 items-center gap-2 px-4 text-sm font-medium text-on-color ${
                  HOT_COLORS[index % HOT_COLORS.length]
                }`}
              >
                {item.query}
                <span className="text-xs" aria-hidden="true">
                  {item.count}
                </span>
                <span className="sr-only">，近 {query.data?.window_days ?? 7} 天被搜索 {item.count} 次</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function GroupHeading({
  id,
  label,
  count,
  children,
}: {
  id: string;
  label: string;
  count: number;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-divider pb-2">
      <h2 id={id} className="flex items-center gap-2">
        <span className="dn-display text-xl">{label}</span>
        <span className="bg-steel px-2 py-0.5 text-xs font-semibold text-on-color">{count}</span>
      </h2>
      {children}
    </div>
  );
}

function CourseGroup({ courses }: { courses: { id: string; name: string; code: string | null; college_name: string | null }[] }) {
  return (
    <section aria-labelledby="search-courses" className="space-y-3">
      <GroupHeading id="search-courses" label="课程" count={courses.length} />
      {courses.length === 0 ? (
        <p className="text-sm text-text-secondary">
          没有匹配的课程。可以换用课程代码，或到课程目录浏览。
        </p>
      ) : (
        <ul className="divide-y divide-divider border border-divider">
          {courses.map((course) => (
            <li key={course.id}>
              <Link
                href={`/courses/${course.id}`}
                className="flex min-h-11 flex-wrap items-center gap-3 px-4 py-3 hover:bg-canvas"
              >
                <BookOpen className="h-4 w-4 shrink-0 text-text-secondary" aria-hidden="true" />
                <span className="min-w-0 flex-1 truncate font-medium text-text-primary">
                  {course.name}
                </span>
                {course.code && (
                  <span className="shrink-0 bg-steel px-2 py-0.5 text-xs font-semibold text-on-color">
                    {course.code}
                  </span>
                )}
                <span className="shrink-0 text-xs text-text-secondary">
                  {course.college_name ?? '归属学院待确认'}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ResourceGroup({
  resources,
  resourceHref,
}: {
  resources: { id: string; title: string; resource_type: string; course_name: string }[];
  resourceHref: string;
}) {
  return (
    <section aria-labelledby="search-resources" className="space-y-3">
      <GroupHeading id="search-resources" label="资料" count={resources.length}>
        <Link
          href={resourceHref}
          className="inline-flex min-h-11 items-center gap-2 text-sm text-brand underline"
        >
          在资料中继续筛选
          <ArrowRight className="h-4 w-4" aria-hidden="true" />
        </Link>
      </GroupHeading>
      {resources.length === 0 ? (
        <p className="text-sm text-text-secondary">
          没有匹配的资料。可以到资料页按类型与年份筛选，或发起求资料。
        </p>
      ) : (
        <ul className="divide-y divide-divider border border-divider">
          {resources.map((resource) => (
            <li key={resource.id}>
              <Link
                href={`/resources/${resource.id}`}
                className="flex min-h-11 items-center gap-3 px-4 py-3 hover:bg-canvas"
              >
                <FileText className="h-4 w-4 shrink-0 text-text-secondary" aria-hidden="true" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium text-text-primary">
                    {resource.title}
                  </span>
                  <span className="mt-0.5 block truncate text-xs text-text-secondary">
                    {resource.course_name} · {resourceTypeLabel(resource.resource_type)}
                  </span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function CreatorGroup({
  creators,
}: {
  creators: { id: string; display_name: string; resource_count: number }[];
}) {
  return (
    <section aria-labelledby="search-creators" className="space-y-3">
      <GroupHeading id="search-creators" label="创作者" count={creators.length} />
      {creators.length === 0 ? (
        <p className="text-sm text-text-secondary">没有匹配的创作者。</p>
      ) : (
        <ul className="divide-y divide-divider border border-divider">
          {creators.map((creator) => (
            <li key={creator.id}>
              <Link
                href={`/creators/${creator.id}`}
                className="flex min-h-11 items-center gap-3 px-4 py-3 hover:bg-canvas"
              >
                <UserRound className="h-4 w-4 shrink-0 text-text-secondary" aria-hidden="true" />
                <span className="min-w-0 flex-1 truncate font-medium text-text-primary">
                  {creator.display_name}
                </span>
                <span className="shrink-0 text-xs text-text-secondary">
                  {creator.resource_count} 份已发布资料
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
