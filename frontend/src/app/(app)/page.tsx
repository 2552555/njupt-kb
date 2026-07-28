'use client';

import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  Search,
  FileText,
  GraduationCap,
  Trophy,
  ArrowRightLeft,
  BookOpen,
  Clock,
  Download,
  Eye,
  Heart,
  Star,
  Flame,
  TrendingUp,
  User,
} from 'lucide-react';
import api from '@/lib/api';
import { formatDate, formatRelative, formatFileSize, formatCount } from '@/lib/utils';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { SkeletonCard, Skeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { StarRating } from '@/components/ui/StarRating';
import { ResourceTypeIcon } from '@/components/ui/ResourceTypeIcon';

const moduleCards = [
  { href: '/resources?type=exam', label: '试卷资料', desc: '历年考试真题', icon: FileText, color: 'bg-blue-500' },
  { href: '/baoyan', label: '保研中心', desc: '推免政策与经验', icon: GraduationCap, color: 'bg-purple-500' },
  { href: '/competitions', label: '竞赛地图', desc: '学科竞赛全攻略', icon: Trophy, color: 'bg-amber-500' },
  { href: '/transfer', label: '转专业', desc: '转专业指南', icon: ArrowRightLeft, color: 'bg-green-500' },
  { href: '/kaoyan', label: '考研中心', desc: '考研数据查询', icon: BookOpen, color: 'bg-rose-500' },
];

export default function HomePage() {
  const router = useRouter();

  const { data: newsData, isLoading: newsLoading } = useQuery({
    queryKey: ['news'],
    queryFn: () => api.get('/scraper/latest?limit=5').then((r) => r.data),
  });

  const { data: popularData, isLoading: popularLoading } = useQuery({
    queryKey: ['popular-resources'],
    queryFn: () => api.get('/resources?sort=downloads&page_size=5').then((r) => r.data),
  });

  const { data: baoyanData, isLoading: baoyanLoading } = useQuery({
    queryKey: ['baoyan-policies'],
    queryFn: () => api.get('/baoyan/policies?year=2026').then((r) => r.data),
  });

  const { data: competitionsData, isLoading: compLoading } = useQuery({
    queryKey: ['competitions'],
    queryFn: () => api.get('/competitions').then((r) => r.data),
  });

  const { data: leaderboardData, isLoading: leaderboardLoading } = useQuery({
    queryKey: ['leaderboard'],
    queryFn: () => api.get('/points/leaderboard?limit=5').then((r) => r.data),
  });

  const { data: newestData, isLoading: newestLoading } = useQuery({
    queryKey: ['newest-resources'],
    queryFn: () => api.get('/resources?sort=newest&page_size=10').then((r) => r.data),
  });

  const news = Array.isArray(newsData) ? newsData : [];
  const popular = Array.isArray(popularData?.items) ? popularData.items : Array.isArray(popularData) ? popularData : [];
  const baoyan = Array.isArray(baoyanData?.items) ? baoyanData.items : Array.isArray(baoyanData) ? baoyanData : [];
  const competitions = Array.isArray(competitionsData?.items) ? competitionsData.items : Array.isArray(competitionsData) ? competitionsData : [];
  const leaderboard = Array.isArray(leaderboardData?.items) ? leaderboardData.items : Array.isArray(leaderboardData) ? leaderboardData : [];
  const newest = Array.isArray(newestData?.items) ? newestData.items : Array.isArray(newestData) ? newestData : [];

  return (
    <div className="space-y-6">
      {/* Hero */}
      <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-indigo-600 via-indigo-500 to-purple-600 p-8 lg:p-12 text-white">
        <div className="relative z-10">
          <h1 className="text-3xl lg:text-4xl font-bold mb-2">
            <span className="text-3xl mr-2">📚</span>南邮知识库
          </h1>
          <p className="text-indigo-100 text-lg mb-6">
            南京邮电大学最大的学生成长资料平台
          </p>
          <div className="max-w-xl">
            <div
              className="flex items-center bg-white/20 backdrop-blur rounded-xl px-4 py-3 cursor-pointer hover:bg-white/30 transition-colors"
              onClick={() => router.push('/search')}
            >
              <Search className="w-5 h-5 text-indigo-200 mr-3" />
              <span className="text-indigo-100">搜索试卷、经验、竞赛...</span>
            </div>
          </div>
        </div>
        <div className="absolute top-0 right-0 w-64 h-64 bg-white/5 rounded-full -translate-y-1/3 translate-x-1/3" />
        <div className="absolute bottom-0 left-1/2 w-48 h-48 bg-white/5 rounded-full translate-y-1/3" />
      </div>

      {/* Module Cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {moduleCards.map((mod) => {
          const Icon = mod.icon;
          return (
            <Link
              key={mod.href}
              href={mod.href}
              className="bg-white rounded-xl border border-gray-200 p-4 hover:shadow-md hover:border-gray-300 transition-all group"
            >
              <div className={`w-10 h-10 rounded-lg ${mod.color} flex items-center justify-center mb-3`}>
                <Icon className="w-5 h-5 text-white" />
              </div>
              <h3 className="text-sm font-semibold text-gray-900">{mod.label}</h3>
              <p className="text-xs text-gray-500 mt-0.5">{mod.desc}</p>
            </Link>
          );
        })}
      </div>

      {/* Two-column: News + Popular */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Latest News */}
        <Card>
          <div className="flex items-center gap-2 mb-4">
            <Clock className="w-5 h-5 text-indigo-600" />
            <h2 className="text-base font-semibold text-gray-900">最新动态</h2>
          </div>
          {newsLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-5 w-full" />
              ))}
            </div>
          ) : news.length === 0 ? (
            <EmptyState icon={Clock} title="暂无动态" description="最新校园动态将在这里显示" />
          ) : (
            <div className="space-y-3">
              {news.slice(0, 5).map((item: Record<string, unknown>, i: number) => (
                <a
                  key={i}
                  href={(item.link as string) || '#'}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="block hover:bg-gray-50 rounded-lg p-2 -mx-2 transition-colors"
                >
                  <p className="text-sm text-gray-900 line-clamp-2">{item.title as string}</p>
                  <p className="text-xs text-gray-400 mt-1">
                    {item.published_at ? formatRelative(item.published_at as string) : ''}
                  </p>
                </a>
              ))}
            </div>
          )}
        </Card>

        {/* Popular Resources */}
        <Card>
          <div className="flex items-center gap-2 mb-4">
            <Flame className="w-5 h-5 text-orange-500" />
            <h2 className="text-base font-semibold text-gray-900">热门资料</h2>
          </div>
          {popularLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : popular.length === 0 ? (
            <EmptyState icon={Flame} title="暂无热门资料" />
          ) : (
            <div className="space-y-2">
              {popular.slice(0, 5).map((item: Record<string, unknown>, i: number) => (
                <Link
                  key={i}
                  href={`/resources/${item.id}`}
                  className="flex items-center gap-3 p-2 -mx-2 rounded-lg hover:bg-gray-50 transition-colors"
                >
                  <ResourceTypeIcon type={(item.resource_type as string) || 'other'} size="sm" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-900 truncate">
                      {item.title as string}
                    </p>
                    <div className="flex items-center gap-3 text-xs text-gray-400 mt-0.5">
                      <span className="flex items-center gap-0.5">
                        <Download className="w-3 h-3" /> {formatCount((item.download_count as number) || 0)}
                      </span>
                      <span className="flex items-center gap-0.5">
                        <Star className="w-3 h-3" /> {(item.avg_rating as number)?.toFixed(1) || '-'}
                      </span>
                    </div>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </Card>

        {/* Baoyan Policies */}
        <Card>
          <div className="flex items-center gap-2 mb-4">
            <GraduationCap className="w-5 h-5 text-purple-600" />
            <h2 className="text-base font-semibold text-gray-900">保研动态</h2>
          </div>
          {baoyanLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-16 w-full" />
              ))}
            </div>
          ) : baoyan.length === 0 ? (
            <EmptyState icon={GraduationCap} title="暂无保研动态" />
          ) : (
            <div className="space-y-3">
              {baoyan.slice(0, 3).map((item: Record<string, unknown>, i: number) => (
                <div key={i} className="p-3 bg-purple-50 rounded-lg">
                  <p className="text-sm font-medium text-purple-900">{item.college_name as string}</p>
                  <p className="text-xs text-purple-600 mt-1">
                    名额: {item.quota as string || '待公布'} | 要求: {(item.requirements as string)?.slice(0, 50) || '详见政策'}...
                  </p>
                </div>
              ))}
            </div>
          )}
        </Card>

        {/* Leaderboard */}
        <Card>
          <div className="flex items-center gap-2 mb-4">
            <TrendingUp className="w-5 h-5 text-green-600" />
            <h2 className="text-base font-semibold text-gray-900">贡献排行榜</h2>
          </div>
          {leaderboardLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : leaderboard.length === 0 ? (
            <EmptyState icon={TrendingUp} title="暂无排名" />
          ) : (
            <div className="space-y-2">
              {leaderboard.slice(0, 5).map((item: Record<string, unknown>, i: number) => (
                <div key={i} className="flex items-center gap-3 p-2">
                  <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold ${
                    i === 0 ? 'bg-amber-100 text-amber-700' :
                    i === 1 ? 'bg-gray-200 text-gray-600' :
                    i === 2 ? 'bg-orange-100 text-orange-700' :
                    'bg-gray-100 text-gray-500'
                  }`}>
                    {i + 1}
                  </div>
                  <div className="w-8 h-8 rounded-full bg-indigo-100 flex items-center justify-center">
                    <User className="w-4 h-4 text-indigo-600" />
                  </div>
                  <div className="flex-1">
                    <p className="text-sm font-medium text-gray-900">{item.display_name as string}</p>
                    <p className="text-xs text-gray-400">{item.points as number} 积分</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      {/* Competitions Section */}
      {competitions.length > 0 && (
        <Card>
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <Trophy className="w-5 h-5 text-amber-600" />
              <h2 className="text-base font-semibold text-gray-900">竞赛列表</h2>
            </div>
            <Link href="/competitions" className="text-sm text-indigo-600 hover:text-indigo-700">
              查看全部 →
            </Link>
          </div>
          {compLoading ? (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {Array.from({ length: 3 }).map((_, i) => <SkeletonCard key={i} />)}
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {competitions.slice(0, 6).map((comp: Record<string, unknown>, i: number) => (
                <div key={i} className="p-4 bg-amber-50 rounded-xl">
                  <h3 className="text-sm font-semibold text-gray-900">{comp.name as string}</h3>
                  <div className="flex items-center gap-2 mt-2">
                    <StarRating rating={(comp.difficulty as number) || 3} size="sm" />
                    <span className="text-xs text-gray-500">
                      {comp.registration_period as string || '待定'}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-1 mt-2">
                    {((comp.suitable_majors as string) || '').split(',').slice(0, 3).map((tag: string, j: number) => (
                      <Badge key={j} color="gray">{tag.trim()}</Badge>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {/* Newest Uploads */}
      <Card>
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <TrendingUp className="w-5 h-5 text-blue-600" />
            <h2 className="text-base font-semibold text-gray-900">最新上传</h2>
          </div>
          <Link href="/resources?sort=newest" className="text-sm text-indigo-600 hover:text-indigo-700">
            查看全部 →
          </Link>
        </div>
        {newestLoading ? (
          <div className="space-y-4">
            {Array.from({ length: 5 }).map((_, i) => <SkeletonCard key={i} />)}
          </div>
        ) : newest.length === 0 ? (
          <EmptyState title="暂无资料" />
        ) : (
          <div className="space-y-3">
            {newest.slice(0, 5).map((item: Record<string, unknown>, i: number) => (
              <Link
                key={i}
                href={`/resources/${item.id}`}
                className="flex items-start gap-4 p-3 -mx-3 rounded-xl hover:bg-gray-50 transition-colors"
              >
                <ResourceTypeIcon type={(item.resource_type as string) || 'other'} size="md" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-900">
                    {item.title as string}
                  </p>
                  <p className="text-xs text-gray-500 mt-1 line-clamp-2">
                    {(item.description as string) || '暂无描述'}
                  </p>
                  <div className="flex items-center gap-4 mt-2 text-xs text-gray-400">
                    <span className="flex items-center gap-0.5">
                      <Download className="w-3 h-3" /> {formatCount((item.download_count as number) || 0)}
                    </span>
                    <span className="flex items-center gap-0.5">
                      <Eye className="w-3 h-3" /> {formatCount((item.view_count as number) || 0)}
                    </span>
                    <span className="flex items-center gap-0.5">
                      <Heart className="w-3 h-3" /> {formatCount((item.like_count as number) || 0)}
                    </span>
                    <span>{formatRelative((item.created_at as string))}</span>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
