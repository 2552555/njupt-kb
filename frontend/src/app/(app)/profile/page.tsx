'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import {
  User,
  Download,
  Upload,
  Star,
  Heart,
  Clock,
  Award,
  TrendingUp,
  Settings,
  Edit,
  FileText,
  MessageSquare,
  Bot,
} from 'lucide-react';
import api from '@/lib/api';
import { formatDate, formatCount, formatFileSize } from '@/lib/utils';
import { useAuthStore } from '@/stores/authStore';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Skeleton, SkeletonCard } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { StarRating } from '@/components/ui/StarRating';
import { ResourceTypeIcon } from '@/components/ui/ResourceTypeIcon';

type TabKey = 'uploads' | 'experiences' | 'favorites' | 'downloads' | 'points' | 'ai-plans';

const TABS: { key: TabKey; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { key: 'uploads', label: '我的上传', icon: Upload },
  { key: 'experiences', label: '我的经验', icon: FileText },
  { key: 'favorites', label: '收藏', icon: Heart },
  { key: 'downloads', label: '下载历史', icon: Download },
  { key: 'points', label: '积分记录', icon: Award },
  { key: 'ai-plans', label: 'AI规划', icon: Bot },
];

function ActivityHeatmap() {
  const days = 90;
  const today = new Date();
  const cells = Array.from({ length: days }).map((_, i) => {
    const date = new Date(today);
    date.setDate(date.getDate() - (days - 1 - i));
    const level = Math.random() > 0.6 ? Math.floor(Math.random() * 3) + 1 : 0;
    return { date, level };
  });

  const levelColors = ['bg-gray-100', 'bg-green-200', 'bg-green-400', 'bg-green-600'];

  return (
    <div className="flex flex-wrap gap-0.5">
      {cells.map((cell, i) => (
        <div
          key={i}
          className={`w-3 h-3 rounded-sm ${levelColors[cell.level]}`}
          title={`${cell.date.toLocaleDateString('zh-CN')}: ${cell.level} 贡献`}
        />
      ))}
    </div>
  );
}

export default function ProfilePage() {
  const { user } = useAuthStore();
  const [activeTab, setActiveTab] = useState<TabKey>('uploads');
  const [page, setPage] = useState(1);

  const endpointMap: Record<TabKey, string> = {
    uploads: '/resources?my=true',
    experiences: '/baoyan/experiences?my=true',
    favorites: '/resources?favorites=true',
    downloads: '/resources?downloads=true',
    points: '/points/history',
    'ai-plans': '/ai/plans',
  };

  const { data, isLoading } = useQuery({
    queryKey: [activeTab, page],
    queryFn: () =>
      api.get(`${endpointMap[activeTab]}&page=${page}&page_size=10`).then((r) => r.data),
  });

  const items = Array.isArray(data?.items) ? data.items : Array.isArray(data) ? data : [];
  const totalPages = data?.total_pages || 1;

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* User header */}
      <Card>
        <div className="flex flex-col sm:flex-row items-start sm:items-center gap-4">
          <div className="w-16 h-16 rounded-full bg-indigo-100 flex items-center justify-center">
            <span className="text-2xl font-bold text-indigo-600">
              {user?.display_name?.charAt(0) || 'U'}
            </span>
          </div>
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <h2 className="text-xl font-bold text-gray-900">{user?.display_name || '用户'}</h2>
              <Badge color={user?.level && user.level >= 5 ? 'purple' : 'indigo'}>
                Lv.{user?.level || 1}
              </Badge>
            </div>
            <p className="text-sm text-gray-500">{user?.email}</p>
            {user?.student_id && (
              <p className="text-xs text-gray-400">学号: {user.student_id}</p>
            )}
          </div>
          <Link href="/upload">
            <Button size="sm">
              <Upload className="w-4 h-4 mr-1.5" /> 上传资料
            </Button>
          </Link>
        </div>
      </Card>

      {/* Stats row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { label: '上传数', value: formatCount(user?.points ? Math.floor(user.points / 10) : 0), icon: Upload, color: 'text-blue-600' },
          { label: '积分', value: formatCount(user?.points || 0), icon: Award, color: 'text-amber-600' },
          { label: '收藏', value: '--', icon: Heart, color: 'text-red-600' },
          { label: '平均评分', value: user?.level ? `${((user.level / 5) * 5).toFixed(1)}` : '--', icon: Star, color: 'text-yellow-600' },
        ].map((stat, i) => {
          const Icon = stat.icon;
          return (
            <Card key={i} padding="sm">
              <div className="flex items-center gap-2">
                <Icon className={`w-4 h-4 ${stat.color}`} />
                <span className="text-xs text-gray-500">{stat.label}</span>
              </div>
              <p className="text-lg font-bold text-gray-900 mt-1">{stat.value}</p>
            </Card>
          );
        })}
      </div>

      {/* Contribution heatmap */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-900 mb-3 flex items-center gap-2">
          <TrendingUp className="w-4 h-4 text-green-600" /> 近期活动
        </h3>
        <ActivityHeatmap />
        <div className="flex items-center justify-between mt-2 text-xs text-gray-400">
          <span>{formatDate(new Date(Date.now() - 90 * 24 * 60 * 60 * 1000))}</span>
          <span>今天</span>
        </div>
      </Card>

      {/* Tabs */}
      <Card padding="none">
        <div className="border-b border-gray-200 overflow-x-auto">
          <div className="flex px-2">
            {TABS.map((tab) => (
              <button
                key={tab.key}
                onClick={() => { setActiveTab(tab.key); setPage(1); }}
                className={`flex items-center gap-1.5 px-4 py-3 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
                  activeTab === tab.key
                    ? 'border-indigo-600 text-indigo-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700'
                }`}
              >
                <tab.icon className="w-4 h-4" />
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {/* Tab Content */}
        <div className="p-4">
          {isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => <SkeletonCard key={i} />)}
            </div>
          ) : items.length === 0 ? (
            <EmptyState
              title={`暂无${TABS.find((t) => t.key === activeTab)?.label || '数据'}`}
              description="还没有相关记录"
            />
          ) : (
            <div className="space-y-3">
              {(activeTab === 'uploads' || activeTab === 'favorites' || activeTab === 'downloads') &&
                items.map((item: Record<string, unknown>, i: number) => (
                  <Link
                    key={i}
                    href={`/resources/${item.id}`}
                    className="flex items-start gap-3 p-3 rounded-xl hover:bg-gray-50 transition-colors"
                  >
                    <ResourceTypeIcon type={(item.resource_type as string) || 'other'} size="sm" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-900 line-clamp-1">
                        {item.title as string}
                      </p>
                      <div className="flex items-center gap-3 mt-1 text-xs text-gray-400">
                        <span className="flex items-center gap-0.5"><Download className="w-3 h-3" /> {formatCount((item.download_count as number) || 0)}</span>
                        <StarRating rating={(item.avg_rating as number) || 0} size="sm" />
                        <span>{formatDate(item.created_at as string)}</span>
                      </div>
                    </div>
                  </Link>
                ))}

              {activeTab === 'experiences' &&
                items.map((item: Record<string, unknown>, i: number) => (
                  <Card key={i} padding="sm" hover>
                    <h4 className="text-sm font-medium text-gray-900">{item.target_university as string}</h4>
                    <div className="flex items-center gap-2 mt-1 text-xs text-gray-500">
                      <span>GPA: {item.gpa as string}</span>
                      <Badge color={(item.offer_result as string) === '录取' ? 'green' : 'gray'}>
                        {item.offer_result as string}
                      </Badge>
                    </div>
                  </Card>
                ))}

              {activeTab === 'points' &&
                items.map((item: Record<string, unknown>, i: number) => (
                  <div key={i} className="flex items-center justify-between p-3 rounded-lg hover:bg-gray-50">
                    <div>
                      <p className="text-sm text-gray-900">{item.description as string || '积分变动'}</p>
                      <p className="text-xs text-gray-400">{formatDate(item.created_at as string)}</p>
                    </div>
                    <Badge color={(item.points as number) > 0 ? 'green' : 'red'}>
                      {(item.points as number) > 0 ? '+' : ''}{item.points as number}
                    </Badge>
                  </div>
                ))}

              {activeTab === 'ai-plans' &&
                items.map((item: Record<string, unknown>, i: number) => (
                  <Card key={i} padding="sm" hover>
                    <div className="flex items-center gap-2">
                      <Bot className="w-4 h-4 text-indigo-600" />
                      <h4 className="text-sm font-medium text-gray-900">{item.title as string || 'AI规划'}</h4>
                    </div>
                    <p className="text-xs text-gray-400 mt-1">{formatDate(item.created_at as string)}</p>
                  </Card>
                ))}
            </div>
          )}

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-2 mt-4">
              <button
                onClick={() => setPage(Math.max(1, page - 1))}
                disabled={page === 1}
                className="px-3 py-1.5 text-sm border border-gray-300 rounded-lg disabled:opacity-50"
              >
                上一页
              </button>
              <span className="text-sm text-gray-600">{page} / {totalPages}</span>
              <button
                onClick={() => setPage(Math.min(totalPages, page + 1))}
                disabled={page === totalPages}
                className="px-3 py-1.5 text-sm border border-gray-300 rounded-lg disabled:opacity-50"
              >
                下一页
              </button>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
