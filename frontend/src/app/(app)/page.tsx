import type { Metadata } from 'next';
import Link from 'next/link';

import { CourseCard } from '@/components/catalog/CourseCard';
import { Notice } from '@/components/common/Notice';
import { SearchBox } from '@/components/common/SearchBox';
import { ResourceCard } from '@/components/resource/ResourceCard';
import type { CourseListItem } from '@/lib/api/catalog';
import type { SearchItem } from '@/lib/api/interaction';
import { serverFetch } from '@/lib/api/server';
import type { CursorPage } from '@/lib/api/types';

export const metadata: Metadata = {
  alternates: { canonical: '/' },
};

// 首页依赖实时目录与已发布内容，不做构建期静态化。
export const dynamic = 'force-dynamic';

export default async function HomePage() {
  const [coursesResult, recentResult] = await Promise.all([
    serverFetch<CursorPage<CourseListItem>>('/courses?limit=6'),
    serverFetch<CursorPage<SearchItem>>('/search?limit=6'),
  ]);

  return (
    <div className="space-y-10">
      {/* Hero：大面积品牌实色色块 + 表达性排版（DESIGN.md §5.1）。 */}
      <section className="bg-brand px-6 py-10 text-on-color md:px-10 md:py-14">
        <p className="dn-kicker">NJUPT COURSE KNOWLEDGE</p>
        <h1 className="dn-display mt-3 max-w-2xl text-4xl md:text-5xl">
          按课程找到可追溯的学习资料
        </h1>
        <p className="mt-4 max-w-xl text-on-color">
          输入课程名、代码或资料标题开始；结果按课程组织，便于判断适用范围后再阅读或下载。
        </p>
        <div className="mt-6 max-w-2xl">
          <SearchBox size="lg" />
        </div>
        <div className="mt-5 flex flex-wrap gap-3">
          <Link
            href="/courses"
            className="inline-flex min-h-11 items-center bg-on-color/15 px-4 text-sm font-medium text-on-color hover:bg-on-color/25"
          >
            浏览全部课程
          </Link>
          <Link
            href="/search"
            className="inline-flex min-h-11 items-center border border-on-color/40 px-4 text-sm font-medium text-on-color hover:bg-on-color/10"
          >
            直接搜索资料
          </Link>
        </div>
      </section>

      <section aria-labelledby="recent-courses">
        <div className="mb-4 flex items-end justify-between gap-4">
          <div>
            <p className="dn-kicker text-text-secondary">COURSES</p>
            <h2 id="recent-courses" className="dn-display text-2xl">
              课程入口
            </h2>
          </div>
          <Link href="/courses" className="text-sm text-text-secondary underline underline-offset-2">
            查看全部
          </Link>
        </div>

        {coursesResult.error ? (
          <Notice tone="warning" title="课程目录暂时无法加载">
            {coursesResult.error.message}。可以稍后刷新，或直接使用搜索。
          </Notice>
        ) : coursesResult.data && coursesResult.data.items.length > 0 ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {coursesResult.data.items.map((course) => (
              <CourseCard key={course.id} course={course} />
            ))}
          </div>
        ) : (
          <Notice tone="info" title="课程目录中还没有可展示的课程">
            目录由维护者导入真实数据后在此显示。你可以先使用搜索查找资料。
          </Notice>
        )}
      </section>

      <section aria-labelledby="recent-resources">
        <div className="mb-4 flex items-end justify-between gap-4">
          <div>
            <p className="dn-kicker text-text-secondary">RECENT</p>
            <h2 id="recent-resources" className="dn-display text-2xl">
              近期内容
            </h2>
          </div>
          <Link href="/search" className="text-sm text-text-secondary underline underline-offset-2">
            更多结果
          </Link>
        </div>

        {recentResult.error ? (
          <Notice tone="warning" title="近期内容暂时无法加载">
            {recentResult.error.message}。这不表示没有资料。
          </Notice>
        ) : recentResult.data && recentResult.data.items.length > 0 ? (
          <div className="grid gap-4 sm:grid-cols-2">
            {recentResult.data.items.map((item) => (
              <ResourceCard
                key={item.resource_id}
                resourceId={item.resource_id}
                title={item.title}
                resourceType={item.resource_type}
                courseName={item.course?.name ?? null}
                courseCode={item.course?.code ?? null}
                freshness={item.freshness_status}
                rating={item.rating}
              />
            ))}
          </div>
        ) : (
          <Notice tone="info" title="还没有已发布的资料">
            资料通过审核并发布后才会出现在这里。你可以在登录后上传自己的资料。
          </Notice>
        )}
      </section>
    </div>
  );
}
