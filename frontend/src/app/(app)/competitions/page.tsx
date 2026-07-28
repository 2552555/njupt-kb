'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Trophy,
  Star,
  Calendar,
  Users,
  Award,
  ChevronDown,
  ChevronUp,
  MapPin,
} from 'lucide-react';
import api from '@/lib/api';
import { formatDate, formatCount } from '@/lib/utils';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { SkeletonCard } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { StarRating } from '@/components/ui/StarRating';

const CATEGORIES = [
  { key: '', label: '全部' },
  { key: '数学类', label: '数学类' },
  { key: '电子类', label: '电子类' },
  { key: '计算机类', label: '计算机类' },
  { key: '创新创业类', label: '创新创业类' },
];

export default function CompetitionsPage() {
  const [category, setCategory] = useState('');
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const { data: competitionsData, isLoading } = useQuery({
    queryKey: ['competitions', category],
    queryFn: () => {
      const params = new URLSearchParams();
      if (category) params.set('category', category);
      return api.get(`/competitions?${params.toString()}`).then((r) => r.data);
    },
  });

  const competitions = Array.isArray(competitionsData?.items)
    ? competitionsData.items
    : Array.isArray(competitionsData)
    ? competitionsData
    : [];

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold text-gray-900">竞赛地图</h1>

      {/* Category tabs */}
      <div className="flex flex-wrap gap-1.5">
        {CATEGORIES.map((cat) => (
          <button
            key={cat.key}
            onClick={() => setCategory(cat.key)}
            className={`px-3 py-1.5 text-xs font-medium rounded-full transition-colors ${
              category === cat.key
                ? 'bg-indigo-600 text-white'
                : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            {cat.label}
          </button>
        ))}
      </div>

      {/* Competition grid */}
      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
        </div>
      ) : competitions.length === 0 ? (
        <EmptyState icon={Trophy} title="暂无竞赛数据" description="竞赛数据正在收集中" />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {competitions.map((comp: Record<string, unknown>, i: number) => {
            const isExpanded = expandedId === (comp.id as number);
            return (
              <Card key={i} hover={!isExpanded} padding={isExpanded ? 'lg' : 'md'}>
                {/* Card header */}
                <div
                  className="cursor-pointer"
                  onClick={() => setExpandedId(isExpanded ? null : (comp.id as number))}
                >
                  <div className="flex items-start justify-between">
                    <Trophy className="w-8 h-8 text-amber-500 flex-shrink-0" />
                    <button className="text-gray-400">
                      {isExpanded ? <ChevronUp className="w-5 h-5" /> : <ChevronDown className="w-5 h-5" />}
                    </button>
                  </div>
                  <h3 className="text-sm font-semibold text-gray-900 mt-3">
                    {comp.name as string}
                  </h3>
                  <div className="flex items-center gap-2 mt-2">
                    <StarRating rating={(comp.difficulty as number) || 3} size="sm" />
                    <span className="text-xs text-gray-400">
                      {comp.category as string}
                    </span>
                  </div>
                  <div className="flex items-center gap-4 mt-3 text-xs text-gray-500">
                    <span className="flex items-center gap-1">
                      <Calendar className="w-3 h-3" />
                      {comp.registration_period as string || '待定'}
                    </span>
                    {comp.njupt_winners_count != null && (
                      <span className="flex items-center gap-1">
                        <Award className="w-3 h-3" />
                        获奖人数: {comp.njupt_winners_count as number}
                      </span>
                    )}
                  </div>
                </div>

                {/* Expanded content */}
                {isExpanded && (
                  <div className="mt-4 pt-4 border-t border-gray-100 space-y-3">
                    {comp.description ? (
                      <div>
                        <h4 className="text-xs font-semibold text-gray-700 mb-1">竞赛简介</h4>
                        <p className="text-sm text-gray-600 leading-relaxed">
                          {String(comp.description)}
                        </p>
                      </div>
                    ) : null}
                    {comp.suitable_majors ? (
                      <div>
                        <h4 className="text-xs font-semibold text-gray-700 mb-1">适合专业</h4>
                        <div className="flex flex-wrap gap-1">
                          {String(comp.suitable_majors).split(',').map((major: string, j: number) => (
                            <Badge key={j} color="blue">{major.trim()}</Badge>
                          ))}
                        </div>
                      </div>
                    ) : null}
                    {comp.resource_links ? (
                      <div>
                        <h4 className="text-xs font-semibold text-gray-700 mb-1">参考资料</h4>
                        <a
                          href={String(comp.resource_links)}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-sm text-indigo-600 hover:text-indigo-700"
                        >
                          查看详情 →
                        </a>
                      </div>
                    ) : null}
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
