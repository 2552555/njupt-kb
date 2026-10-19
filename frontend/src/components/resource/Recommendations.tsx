import { Notice } from '@/components/common/Notice';
import { ResourceCard } from '@/components/resource/ResourceCard';
import type { RecommendationsOut } from '@/lib/api/interaction';
import { serverFetch } from '@/lib/api/server';

export async function Recommendations({ courseId }: { courseId?: string }) {
  const result = await serverFetch<RecommendationsOut>(
    `/recommendations?limit=6${courseId ? `&course_id=${courseId}` : ''}`,
  );
  return (
    <section aria-label={courseId ? '课程推荐资料' : '推荐资料'}>
      <p className="dn-kicker text-text-secondary">RECOMMENDED</p>
      <h2 className="dn-display text-2xl">{courseId ? '课程推荐资料' : '推荐资料'}</h2>
      <div className="mt-4">
        {result.error ? (
          <Notice tone="warning" title="推荐暂时无法加载">可以继续浏览课程或搜索资料。</Notice>
        ) : result.data && result.data.items.length > 0 ? (
          <ul className="dn-stagger grid gap-4 sm:grid-cols-2">
            {result.data.items.map((item) => (
              <li key={item.resource_id}>
                <ResourceCard resourceId={item.resource_id} title={item.title}
                  resourceType={item.resource_type} courseName={item.course?.name}
                  courseCode={item.course?.code} freshness={item.freshness_status} rating={item.rating} />
                <p className="mt-2 flex flex-wrap items-center gap-2 text-xs text-text-secondary">
                  {item.explore && (
                    <span className="border border-control-border px-1.5 py-0.5 text-[0.6875rem] font-semibold text-text-primary">
                      探索位
                    </span>
                  )}
                  <span>{item.reasons.join(' · ')}</span>
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <Notice tone="info" title="暂时没有可推荐的资料">可以继续浏览课程或搜索资料。</Notice>
        )}
      </div>
    </section>
  );
}
