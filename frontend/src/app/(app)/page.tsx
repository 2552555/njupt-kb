import type { Metadata } from 'next';
import Link from 'next/link';

import { CourseCard } from '@/components/catalog/CourseCard';
import { Notice } from '@/components/common/Notice';
import { SearchBox } from '@/components/common/SearchBox';
import { ResourceCard } from '@/components/resource/ResourceCard';
import { Recommendations } from '@/components/resource/Recommendations';
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
    serverFetch<CursorPage<SearchItem>>('/search/resources?limit=6'),
  ]);

  return (
    <div className="space-y-10">
      <section className="bg-brand px-6 py-6 text-on-color md:px-10 md:py-8">
        <p className="dn-kicker">NJUPT COURSE KNOWLEDGE</p>
        <h1 className="dn-display mt-3 max-w-2xl text-3xl md:text-4xl">
          课程资料
        </h1>
        <div className="mt-6 max-w-2xl">
          <SearchBox size="lg" />
        </div>
        <div className="mt-5 flex flex-wrap gap-3">
          <Link
            href="/courses"
            className="inline-flex min-h-11 items-center bg-on-color/15 px-4 text-sm font-medium text-on-color hover:bg-on-color/25"
          >
            全部课程
          </Link>
          <Link
            href="/requests?new=1"
            className="inline-flex min-h-11 items-center border border-on-color/40 px-4 text-sm font-medium text-on-color hover:bg-on-color/10"
          >
            求资料
          </Link>
        </div>
      </section>

      <nav aria-label="常用操作" className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          ['/me/favorites', '我的收藏'],
          ['/me/follows', '我的关注'],
          ['/upload', '上传资料'],
          ['/me/resources', '我的资料'],
        ].map(([href, title]) => (
          <Link key={href} href={href} className="border border-divider bg-surface p-4 hover:bg-canvas">
            <span className="font-medium text-text-primary">{title}</span>
          </Link>
        ))}
      </nav>

      <section aria-labelledby="recent-courses">
        <div className="mb-4 flex items-end justify-between gap-4">
          <div>
            <p className="dn-kicker text-text-secondary">COURSES</p>
            <h2 id="recent-courses" className="dn-display text-2xl">
              课程
            </h2>
          </div>
          <Link href="/courses" className="text-sm text-text-secondary underline underline-offset-2">
            查看全部
          </Link>
        </div>

        {coursesResult.error ? (
          <Notice tone="warning" title="课程目录暂时无法加载">
            {coursesResult.error.message}。可稍后刷新或用搜索。
          </Notice>
        ) : coursesResult.data && coursesResult.data.items.length > 0 ? (
          <div className="dn-stagger grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {coursesResult.data.items.map((course) => (
              <CourseCard key={course.id} course={course} />
            ))}
          </div>
        ) : (
          <Notice tone="info" title="课程目录中还没有可展示的课程">
            目录由维护者导入；可先使用搜索。
          </Notice>
        )}
      </section>

      <Recommendations />

      <section aria-labelledby="recent-resources">
        <div className="mb-4 flex items-end justify-between gap-4">
          <div>
            <p className="dn-kicker text-text-secondary">RECENT</p>
            <h2 id="recent-resources" className="dn-display text-2xl">
              最新资料
            </h2>
          </div>
          <Link href="/resources" className="text-sm text-text-secondary underline underline-offset-2">
            全部资料
          </Link>
        </div>

        {recentResult.error ? (
          <Notice tone="warning" title="最新资料暂时无法加载">
            {recentResult.error.message}。请稍后重试。
          </Notice>
        ) : recentResult.data && recentResult.data.items.length > 0 ? (
          <div className="dn-stagger grid gap-4 sm:grid-cols-2">
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
            审核并发布后显示在这里；登录后可上传资料。
          </Notice>
        )}
      </section>
    </div>
  );
}
