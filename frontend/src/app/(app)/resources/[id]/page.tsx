'use client';

import { useState } from 'react';
import { useParams } from 'next/navigation';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Download,
  Heart,
  Eye,
  Star,
  Calendar,
  FileText,
  User,
  ChevronRight,
  MessageSquare,
} from 'lucide-react';
import api from '@/lib/api';
import { formatDate, formatFileSize, formatCount, getResourceTypeInfo } from '@/lib/utils';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { StarRating } from '@/components/ui/StarRating';
import { ResourceTypeIcon } from '@/components/ui/ResourceTypeIcon';
import toast from 'react-hot-toast';

export default function ResourceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const [commentText, setCommentText] = useState('');
  const [commentRating, setCommentRating] = useState(5);

  const { data: resource, isLoading } = useQuery({
    queryKey: ['resource', id],
    queryFn: () => api.get(`/resources/${id}`).then((r) => r.data),
  });

  const { data: commentsData } = useQuery({
    queryKey: ['comments', id],
    queryFn: () => api.get(`/resources/${id}/comments`).then((r) => r.data),
  });

  const likeMutation = useMutation({
    mutationFn: () => api.post(`/resources/${id}/like`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['resource', id] });
      toast.success('已点赞');
    },
  });

  const favoriteMutation = useMutation({
    mutationFn: () => api.post(`/resources/${id}/favorite`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['resource', id] });
      toast.success('已收藏');
    },
  });

  const commentMutation = useMutation({
    mutationFn: (data: { content: string; rating: number }) =>
      api.post(`/resources/${id}/comments`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['comments', id] });
      setCommentText('');
      toast.success('评论发表成功');
    },
    onError: () => toast.error('评论发表失败'),
  });

  const r = resource as Record<string, unknown> | null;
  const comments = Array.isArray(commentsData?.items) ? commentsData.items : Array.isArray(commentsData) ? commentsData : [];
  const typeInfo = getResourceTypeInfo((r?.resource_type as string) || 'other');

  if (isLoading) {
    return (
      <div className="max-w-5xl mx-auto space-y-6">
        <Skeleton className="h-6 w-48" />
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 space-y-4">
            <Skeleton className="h-48 w-full" />
            <Skeleton className="h-32 w-full" />
          </div>
          <Skeleton className="h-64 w-full" />
        </div>
      </div>
    );
  }

  if (!r) {
    return <EmptyState title="资料不存在" description="该资料可能已被删除" />;
  }

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      {/* Breadcrumb */}
      <div className="flex items-center gap-1 text-sm text-gray-500">
        <span>资源中心</span>
        <ChevronRight className="w-3 h-3" />
        <span className="text-gray-900 font-medium line-clamp-1">{r.title as string}</span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Main content */}
        <div className="lg:col-span-2 space-y-6">
          {/* Title & Description */}
          <Card>
            <div className="flex items-start gap-4">
              <ResourceTypeIcon type={(r.resource_type as string) || 'other'} size="lg" />
              <div className="flex-1">
                <h1 className="text-xl font-bold text-gray-900">{r.title as string}</h1>
                <div className="flex flex-wrap items-center gap-2 mt-2">
                  <Badge color={typeInfo.color.includes('blue') ? 'blue' : 'gray'}>{typeInfo.label}</Badge>
                  {(r.course_name as string) && <Badge color="purple">{r.course_name as string}</Badge>}
                </div>
                <p className="text-sm text-gray-600 mt-4 leading-relaxed">
                  {r.description as string || '暂无描述'}
                </p>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-3 mt-6 pt-4 border-t border-gray-100">
              <Button onClick={() => window.open(`/api/resources/${id}/download`, '_blank')}>
                <Download className="w-4 h-4 mr-1.5" /> 下载 ({formatFileSize((r.file_size as number) || 0)})
              </Button>
              <Button variant="secondary" onClick={() => likeMutation.mutate()}>
                <Heart className="w-4 h-4 mr-1.5" /> {formatCount((r.like_count as number) || 0)}
              </Button>
              <Button variant="secondary" onClick={() => favoriteMutation.mutate()}>
                <Star className="w-4 h-4 mr-1.5" /> 收藏
              </Button>
            </div>
          </Card>

          {/* Comments */}
          <Card>
            <h2 className="text-base font-semibold text-gray-900 mb-4 flex items-center gap-2">
              <MessageSquare className="w-5 h-5" /> 评论 ({comments.length})
            </h2>

            {/* Comment form */}
            <div className="mb-6 p-4 bg-gray-50 rounded-xl">
              <div className="flex items-center gap-2 mb-3">
                <span className="text-sm text-gray-600">评分：</span>
                <div className="flex gap-0.5">
                  {[1, 2, 3, 4, 5].map((star) => (
                    <button
                      key={star}
                      onClick={() => setCommentRating(star)}
                      className="focus:outline-none"
                    >
                      <Star
                        className={`w-5 h-5 ${
                          star <= commentRating
                            ? 'text-amber-400 fill-amber-400'
                            : 'text-gray-300'
                        }`}
                      />
                    </button>
                  ))}
                </div>
              </div>
              <textarea
                value={commentText}
                onChange={(e) => setCommentText(e.target.value)}
                placeholder="写下你的评论..."
                className="w-full border border-gray-300 rounded-lg p-3 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none resize-none"
                rows={3}
              />
              <div className="flex justify-end mt-2">
                <Button
                  size="sm"
                  onClick={() => commentMutation.mutate({ content: commentText, rating: commentRating })}
                  isLoading={commentMutation.isPending}
                  disabled={!commentText.trim()}
                >
                  发表评论
                </Button>
              </div>
            </div>

            {/* Comments list */}
            {comments.length === 0 ? (
              <EmptyState title="暂无评论" description="来第一个评论吧！" />
            ) : (
              <div className="space-y-4">
                {comments.map((c: Record<string, unknown>, i: number) => (
                  <div key={i} className="flex gap-3 p-3 rounded-lg hover:bg-gray-50">
                    <div className="w-8 h-8 rounded-full bg-indigo-100 flex items-center justify-center flex-shrink-0">
                      <span className="text-xs font-medium text-indigo-600">
                        {(c.user_name as string)?.charAt(0) || 'U'}
                      </span>
                    </div>
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-gray-900">{c.user_name as string}</span>
                        <StarRating rating={(c.rating as number) || 0} size="sm" />
                        <span className="text-xs text-gray-400">{formatDate(c.created_at as string)}</span>
                      </div>
                      <p className="text-sm text-gray-600 mt-1">{c.content as string}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>

        {/* Sidebar */}
        <div className="space-y-4">
          {/* File info */}
          <Card>
            <h3 className="text-sm font-semibold text-gray-900 mb-3">文件信息</h3>
            <div className="space-y-2 text-sm">
              <div className="flex items-center gap-2 text-gray-600">
                <Badge>{typeInfo.label}</Badge>
              </div>
              <p className="text-gray-600">
                <span className="text-gray-400">大小：</span>{formatFileSize((r.file_size as number) || 0)}
              </p>
              {r.pages ? <p className="text-gray-600">
                <span className="text-gray-400">页数：</span>{String(r.pages)} 页
              </p> : null}
              <p className="text-gray-600">
                <span className="text-gray-400">上传时间：</span>{formatDate(r.created_at as string)}
              </p>
            </div>
            <div className="flex items-center gap-4 mt-4 pt-3 border-t border-gray-100 text-xs text-gray-400">
              <span className="flex items-center gap-1"><Eye className="w-3 h-3" /> {formatCount((r.view_count as number) || 0)}</span>
              <span className="flex items-center gap-1"><Download className="w-3 h-3" /> {formatCount((r.download_count as number) || 0)}</span>
              <span className="flex items-center gap-1"><Heart className="w-3 h-3" /> {formatCount((r.like_count as number) || 0)}</span>
            </div>
          </Card>

          {/* Uploader */}
          <Card>
            <h3 className="text-sm font-semibold text-gray-900 mb-3">上传者</h3>
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-full bg-indigo-100 flex items-center justify-center">
                <User className="w-5 h-5 text-indigo-600" />
              </div>
              <div>
                <p className="text-sm font-medium text-gray-900">
                  {r.uploader_name as string || '匿名用户'}
                </p>
                <p className="text-xs text-gray-500">
                  {r.uploader_points as string || 0} 积分
                </p>
              </div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
