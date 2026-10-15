import Link from 'next/link';

import { ResourceTypeIcon } from '@/components/ui/ResourceTypeIcon';
import { StarRating } from '@/components/ui/StarRating';
import { RESOURCE_TYPE_LABELS, type FreshnessStatus, type ResourceType } from '@/lib/api/types';

const freshnessLabels: Record<FreshnessStatus, { label: string; className: string }> = {
  current: { label: '现行内容', className: 'bg-emerald text-on-color' },
  possibly_outdated: { label: '可能过时', className: 'bg-amber text-on-color' },
  outdated: { label: '已过时', className: 'bg-crimson text-on-color' },
};

export interface ResourceCardProps {
  resourceId: string;
  title: string;
  resourceType?: ResourceType | null;
  courseName?: string | null;
  courseCode?: string | null;
  freshness?: FreshnessStatus | string | null;
  rating?: { average: number; count: number } | null;
}

/** 资料条目：类型图标 + 标题 + 课程归属 + 时效/评分，链接到资料详情。 */
export function ResourceCard({
  resourceId,
  title,
  resourceType,
  courseName,
  courseCode,
  freshness,
  rating,
}: ResourceCardProps) {
  const freshnessInfo =
    freshness && freshness in freshnessLabels
      ? freshnessLabels[freshness as FreshnessStatus]
      : null;

  return (
    <Link
      href={`/resources/${resourceId}`}
      className="flex gap-3 border border-divider bg-surface p-4 transition-shadow duration-200 hover:shadow-2"
    >
      <ResourceTypeIcon type={resourceType ?? 'other'} />
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium text-text-primary">{title}</p>
        <p className="mt-1 truncate text-xs text-text-secondary">
          {courseName ? (
            <>
              {courseName}
              {courseCode ? ` · ${courseCode}` : ''}
            </>
          ) : (
            '课程归属待确认'
          )}
          {resourceType ? ` · ${RESOURCE_TYPE_LABELS[resourceType]}` : ''}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          {freshnessInfo && (
            <span className={`px-2 py-0.5 text-xs font-semibold ${freshnessInfo.className}`}>
              {freshnessInfo.label}
            </span>
          )}
          {rating && rating.count > 0 ? (
            <span className="flex items-center gap-1.5 text-xs text-text-secondary">
              <StarRating rating={rating.average} size="sm" />
              {rating.average.toFixed(1)}（{rating.count} 人）
            </span>
          ) : (
            <span className="text-xs text-text-secondary">暂无评分</span>
          )}
        </div>
      </div>
    </Link>
  );
}
