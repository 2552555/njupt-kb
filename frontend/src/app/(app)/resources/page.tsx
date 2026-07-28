'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { useSearchParams, useRouter } from 'next/navigation';
import {
  Search,
  Download,
  Eye,
  Heart,
  Star,
  Upload,
  SlidersHorizontal,
  ChevronRight,
  X,
} from 'lucide-react';
import api from '@/lib/api';
import { formatDate, formatRelative, formatFileSize, formatCount, getResourceTypeInfo } from '@/lib/utils';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { SkeletonCard, Skeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { StarRating } from '@/components/ui/StarRating';
import { ResourceTypeIcon } from '@/components/ui/ResourceTypeIcon';

const RESOURCE_TYPE_OPTIONS = [
  { value: '', label: '全部' },
  { value: 'exam', label: '试卷' },
  { value: 'ppt', label: 'PPT课件' },
  { value: 'note', label: '笔记' },
  { value: 'review', label: '复习资料' },
  { value: 'lab_report', label: '实验报告' },
  { value: 'textbook', label: '教材' },
  { value: 'code', label: '代码' },
  { value: 'other', label: '其他' },
];

const SORT_OPTIONS = [
  { value: 'newest', label: '最新上传' },
  { value: 'downloads', label: '最多下载' },
  { value: 'rating', label: '最高评分' },
  { value: 'likes', label: '最多喜欢' },
];

export default function ResourcesPage() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const [search, setSearch] = useState(searchParams.get('q') || '');
  const [selectedCollege, setSelectedCollege] = useState<number | null>(null);
  const [selectedMajor, setSelectedMajor] = useState<number | null>(null);
  const [selectedCourse, setSelectedCourse] = useState<number | null>(null);
  const [resourceType, setResourceType] = useState(searchParams.get('type') || '');
  const [sort, setSort] = useState('newest');
  const [page, setPage] = useState(1);

  // Colleges
  const { data: colleges } = useQuery({
    queryKey: ['colleges'],
    queryFn: () => api.get('/colleges').then((r) => r.data),
  });

  // Majors for selected college
  const { data: majors } = useQuery({
    queryKey: ['majors', selectedCollege],
    queryFn: () => api.get(`/majors?college_id=${selectedCollege}`).then((r) => r.data),
    enabled: !!selectedCollege,
  });

  // Courses for selected major
  const { data: courses } = useQuery({
    queryKey: ['courses', selectedMajor],
    queryFn: () => api.get(`/courses?major_id=${selectedMajor}`).then((r) => r.data),
    enabled: !!selectedMajor,
  });

  // Resources
  const { data: resourcesData, isLoading: resourcesLoading } = useQuery({
    queryKey: ['resources', selectedCourse, resourceType, sort, page, search],
    queryFn: () => {
      const params = new URLSearchParams();
      if (selectedCourse) params.set('course_id', String(selectedCourse));
      if (resourceType) params.set('resource_type', resourceType);
      params.set('sort', sort);
      params.set('page', String(page));
      params.set('page_size', '20');
      if (search) params.set('q', search);
      return api.get(`/resources?${params.toString()}`).then((r) => r.data);
    },
  });

  const collegeList = Array.isArray(colleges) ? colleges : [];
  const majorList = Array.isArray(majors) ? majors : [];
  const courseList = Array.isArray(courses) ? courses : [];
  const resources = Array.isArray(resourcesData?.items) ? resourcesData.items : Array.isArray(resourcesData) ? resourcesData : [];
  const totalPages = resourcesData?.total_pages || 1;

  const selectedCollegeName = collegeList.find((c: Record<string, unknown>) => c.id === selectedCollege)?.name as string;
  const selectedMajorName = majorList.find((m: Record<string, unknown>) => m.id === selectedMajor)?.name as string;
  const selectedCourseName = courseList.find((c: Record<string, unknown>) => c.id === selectedCourse)?.name as string;

  const breadcrumbs = [
    { label: '资源中心', href: '/resources' },
    selectedCollegeName && { label: selectedCollegeName, href: '#' },
    selectedMajorName && { label: selectedMajorName, href: '#' },
    selectedCourseName && { label: selectedCourseName, href: '#' },
  ].filter(Boolean) as { label: string; href: string }[];

  const handleSearch = () => {
    setPage(1);
    // triggers refetch via queryKey
  };

  return (
    <div className="space-y-6">
      {/* Search & Filter Bar */}
      <div className="flex items-center gap-3">
        <div className="flex-1 max-w-md">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              type="text"
              placeholder="搜索资料..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              className="w-full pl-10 pr-4 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none"
            />
          </div>
        </div>
        <Link href="/upload">
          <Button variant="primary" size="sm">
            <Upload className="w-4 h-4 mr-1.5" /> 上传
          </Button>
        </Link>
      </div>

      {/* Breadcrumb */}
      {breadcrumbs.length > 1 && (
        <div className="flex items-center gap-1 text-sm text-gray-500">
          {breadcrumbs.map((crumb, i) => (
            <span key={i} className="flex items-center gap-1">
              {i > 0 && <ChevronRight className="w-3 h-3" />}
              {i === breadcrumbs.length - 1 ? (
                <span className="text-gray-900 font-medium">{crumb.label}</span>
              ) : (
                <Link href={crumb.href} className="hover:text-indigo-600">
                  {crumb.label}
                </Link>
              )}
            </span>
          ))}
        </div>
      )}

      {/* Type Tabs & Sort */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-1.5">
          {RESOURCE_TYPE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => { setResourceType(opt.value); setPage(1); }}
              className={`px-3 py-1.5 text-xs font-medium rounded-full transition-colors ${
                resourceType === opt.value
                  ? 'bg-indigo-600 text-white'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <select
          value={sort}
          onChange={(e) => { setSort(e.target.value); setPage(1); }}
          className="text-sm border border-gray-300 rounded-lg px-3 py-1.5 bg-white"
        >
          {SORT_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </div>

      <div className="flex flex-col lg:flex-row gap-6">
        {/* Left: College/Major/Course sidebar */}
        <div className="lg:w-56 flex-shrink-0">
          <Card padding="sm">
            <h3 className="text-sm font-semibold text-gray-900 mb-3 px-1">学院列表</h3>
            <div className="space-y-0.5 max-h-96 overflow-y-auto">
              <button
                onClick={() => {
                  setSelectedCollege(null);
                  setSelectedMajor(null);
                  setSelectedCourse(null);
                  setPage(1);
                }}
                className={`w-full text-left px-2 py-1.5 text-sm rounded-md transition-colors ${
                  !selectedCollege ? 'bg-indigo-50 text-indigo-700 font-medium' : 'text-gray-600 hover:bg-gray-50'
                }`}
              >
                全部学院
              </button>
              {collegeList.map((college: Record<string, unknown>) => (
                <div key={college.id as number}>
                  <button
                    onClick={() => {
                      setSelectedCollege(college.id as number);
                      setSelectedMajor(null);
                      setSelectedCourse(null);
                      setPage(1);
                    }}
                    className={`w-full text-left px-2 py-1.5 text-sm rounded-md transition-colors ${
                      selectedCollege === college.id ? 'bg-indigo-50 text-indigo-700 font-medium' : 'text-gray-600 hover:bg-gray-50'
                    }`}
                  >
                    {college.name as string}
                  </button>
                  {selectedCollege === college.id && majorList.length > 0 && (
                    <div className="ml-4 mt-0.5 space-y-0.5">
                      {majorList.map((major: Record<string, unknown>) => (
                        <div key={major.id as number}>
                          <button
                            onClick={() => {
                              setSelectedMajor(major.id as number);
                              setSelectedCourse(null);
                              setPage(1);
                            }}
                            className={`w-full text-left px-2 py-1.5 text-xs rounded-md transition-colors ${
                              selectedMajor === major.id ? 'bg-indigo-50 text-indigo-700 font-medium' : 'text-gray-500 hover:bg-gray-50'
                            }`}
                          >
                            {major.name as string}
                          </button>
                          {selectedMajor === major.id && courseList.length > 0 && (
                            <div className="ml-3 mt-0.5 space-y-0.5">
                              {courseList.map((course: Record<string, unknown>) => (
                                <button
                                  key={course.id as number}
                                  onClick={() => {
                                    setSelectedCourse(course.id as number);
                                    setPage(1);
                                  }}
                                  className={`w-full text-left px-2 py-1 text-xs rounded-md transition-colors ${
                                    selectedCourse === course.id ? 'bg-indigo-50 text-indigo-700 font-medium' : 'text-gray-400 hover:bg-gray-50'
                                  }`}
                                >
                                  {course.name as string}
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </Card>
        </div>

        {/* Right: Resource List */}
        <div className="flex-1">
          {resourcesLoading ? (
            <div className="space-y-4">
              {Array.from({ length: 5 }).map((_, i) => <SkeletonCard key={i} />)}
            </div>
          ) : resources.length === 0 ? (
            <EmptyState
              title="暂无资料"
              description="该分类下还没有资料，来上传第一个吧！"
              actionLabel="上传资料"
              onAction={() => router.push('/upload')}
            />
          ) : (
            <>
              <div className="space-y-4">
                {resources.map((item: Record<string, unknown>, i: number) => {
                  const typeInfo = getResourceTypeInfo(item.resource_type as string);
                  return (
                    <Link
                      key={i}
                      href={`/resources/${item.id}`}
                      className="block bg-white rounded-xl border border-gray-200 p-5 hover:shadow-md hover:border-gray-300 transition-all"
                    >
                      <div className="flex items-start gap-4">
                        <ResourceTypeIcon type={(item.resource_type as string) || 'other'} size="md" />
                        <div className="flex-1 min-w-0">
                          <h3 className="text-sm font-semibold text-gray-900 line-clamp-1">
                            {item.title as string}
                          </h3>
                          <p className="text-xs text-gray-500 mt-1 line-clamp-2">
                            {item.description as string || '暂无描述'}
                          </p>
                          <div className="flex flex-wrap items-center gap-3 mt-3 text-xs text-gray-400">
                            <span className="flex items-center gap-0.5">
                              <Download className="w-3 h-3" /> {formatCount((item.download_count as number) || 0)}
                            </span>
                            <span className="flex items-center gap-0.5">
                              <Eye className="w-3 h-3" /> {formatCount((item.view_count as number) || 0)}
                            </span>
                            <span className="flex items-center gap-0.5">
                              <Heart className="w-3 h-3" /> {formatCount((item.like_count as number) || 0)}
                            </span>
                            <StarRating rating={(item.avg_rating as number) || 0} size="sm" showValue />
                            <Badge color="gray">{typeInfo.label}</Badge>
                            <span>{formatRelative(item.created_at as string)}</span>
                          </div>
                        </div>
                      </div>
                    </Link>
                  );
                })}
              </div>

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex items-center justify-center gap-2 mt-6">
                  <button
                    onClick={() => setPage(Math.max(1, page - 1))}
                    disabled={page === 1}
                    className="px-3 py-1.5 text-sm border border-gray-300 rounded-lg disabled:opacity-50"
                  >
                    上一页
                  </button>
                  <span className="text-sm text-gray-600">
                    {page} / {totalPages}
                  </span>
                  <button
                    onClick={() => setPage(Math.min(totalPages, page + 1))}
                    disabled={page === totalPages}
                    className="px-3 py-1.5 text-sm border border-gray-300 rounded-lg disabled:opacity-50"
                  >
                    下一页
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
