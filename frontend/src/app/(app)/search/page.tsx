'use client';

import { useState, useEffect } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import {
  Search,
  Download,
  Eye,
  Heart,
  FileText,
  GraduationCap,
  Trophy,
  ArrowRightLeft,
} from 'lucide-react';
import api from '@/lib/api';
import { formatDate, formatCount, getResourceTypeInfo } from '@/lib/utils';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { SkeletonCard } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { StarRating } from '@/components/ui/StarRating';
import { ResourceTypeIcon } from '@/components/ui/ResourceTypeIcon';

const TYPE_FILTERS = [
  { key: '', label: '全部', icon: Search },
  { key: 'resource', label: '资源', icon: FileText },
  { key: 'baoyan', label: '保研经验', icon: GraduationCap },
  { key: 'competition', label: '竞赛', icon: Trophy },
  { key: 'transfer', label: '转专业案例', icon: ArrowRightLeft },
];

export default function SearchPage() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const initialQuery = searchParams.get('q') || '';
  const [searchInput, setSearchInput] = useState(initialQuery);
  const [query, setQuery] = useState(initialQuery);
  const [typeFilter, setTypeFilter] = useState('');

  useEffect(() => {
    setSearchInput(initialQuery);
    setQuery(initialQuery);
  }, [initialQuery]);

  const { data, isLoading } = useQuery({
    queryKey: ['search', query, typeFilter],
    queryFn: () => {
      if (!query.trim()) return { items: [] };
      const params = new URLSearchParams();
      params.set('q', query);
      if (typeFilter) params.set('type', typeFilter);
      params.set('page_size', '20');
      return api.get(`/search?${params.toString()}`).then((r) => r.data);
    },
    enabled: !!query.trim(),
  });

  const results = Array.isArray(data?.items) ? data.items : Array.isArray(data) ? data : [];

  const handleSearch = () => {
    if (searchInput.trim()) {
      setQuery(searchInput);
      router.push(`/search?q=${encodeURIComponent(searchInput)}`);
    }
  };

  const highlightText = (text: string, highlight: string) => {
    if (!highlight || !text) return text;
    const regex = new RegExp(`(${highlight.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
    const parts = text.split(regex);
    return parts.map((part, i) =>
      regex.test(part) ? (
        <mark key={i} className="bg-yellow-200 text-yellow-900 rounded px-0.5">
          {part}
        </mark>
      ) : (
        part
      )
    );
  };

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Search input */}
      <div className="flex gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
          <input
            type="text"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
            placeholder="搜索试卷、经验、竞赛..."
            className="w-full pl-12 pr-4 py-3 text-base border border-gray-300 rounded-xl focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none shadow-sm"
          />
        </div>
        <button
          onClick={handleSearch}
          className="px-6 py-3 bg-indigo-600 text-white rounded-xl hover:bg-indigo-700 transition-colors font-medium text-sm"
        >
          搜索
        </button>
      </div>

      {/* Type filters */}
      <div className="flex flex-wrap gap-1.5">
        {TYPE_FILTERS.map((f) => {
          const Icon = f.icon;
          return (
            <button
              key={f.key}
              onClick={() => setTypeFilter(f.key)}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-full transition-colors ${
                typeFilter === f.key
                  ? 'bg-indigo-600 text-white'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              {f.label}
            </button>
          );
        })}
      </div>

      {/* Results */}
      {!query.trim() ? (
        <EmptyState
          icon={Search}
          title="搜索南邮知识库"
          description="输入关键词搜索试卷、保研经验、竞赛资料等"
        />
      ) : isLoading ? (
        <div className="space-y-4">
          {Array.from({ length: 5 }).map((_, i) => <SkeletonCard key={i} />)}
        </div>
      ) : results.length === 0 ? (
        <EmptyState
          icon={Search}
          title="未找到相关结果"
          description={`没有找到与「${query}」相关的内容，请尝试其他关键词`}
        />
      ) : (
        <div className="space-y-4">
          <p className="text-sm text-gray-500">
            找到 <span className="font-medium text-gray-900">{results.length}</span> 条与「
            <span className="font-medium text-gray-900">{query}</span>」相关的结果
          </p>
          {results.map((item: Record<string, unknown>, i: number) => {
            const itemType = item.type as string || 'resource';
            const typeInfo = getResourceTypeInfo((item.resource_type as string) || 'other');
            const href =
              itemType === 'baoyan'
                ? '/baoyan'
                : itemType === 'competition'
                ? '/competitions'
                : itemType === 'transfer'
                ? '/transfer'
                : `/resources/${item.id}`;

            return (
              <Link
                key={i}
                href={href}
                className="block bg-white rounded-xl border border-gray-200 p-5 hover:shadow-md hover:border-gray-300 transition-all"
              >
                <div className="flex items-start gap-4">
                  <ResourceTypeIcon type={(item.resource_type as string) || 'other'} size="md" />
                  <div className="flex-1 min-w-0">
                    <h3 className="text-sm font-semibold text-gray-900 line-clamp-1">
                      {highlightText(item.title as string || '', query)}
                    </h3>
                    {item.description ? (
                      <p className="text-xs text-gray-500 mt-1 line-clamp-2">
                        {highlightText(String(item.description), query)}
                      </p>
                    ) : null}
                    <div className="flex flex-wrap items-center gap-3 mt-2 text-xs text-gray-400">
                      {item.download_count != null && (
                        <span className="flex items-center gap-0.5">
                          <Download className="w-3 h-3" /> {formatCount(item.download_count as number)}
                        </span>
                      )}
                      {item.view_count != null && (
                        <span className="flex items-center gap-0.5">
                          <Eye className="w-3 h-3" /> {formatCount(item.view_count as number)}
                        </span>
                      )}
                      {item.like_count != null && (
                        <span className="flex items-center gap-0.5">
                          <Heart className="w-3 h-3" /> {formatCount(item.like_count as number)}
                        </span>
                      )}
                      {item.avg_rating != null && (
                        <StarRating rating={item.avg_rating as number} size="sm" showValue />
                      )}
                      {item.resource_type ? (
                        <Badge color="gray">{typeInfo.label}</Badge>
                      ) : null}
                      {item.created_at ? (
                        <span>{formatDate(String(item.created_at))}</span>
                      ) : null}
                    </div>
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
