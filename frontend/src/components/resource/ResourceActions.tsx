'use client';

import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Download, Eye, Flag, Heart, Star } from 'lucide-react';

import { Notice } from '@/components/common/Notice';
import { CoursePicker } from '@/components/catalog/CoursePicker';
import { RESOURCE_TYPE_LABELS, RESOURCE_TYPE_ORDER, type ResourceType } from '@/lib/api/types';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { Skeleton } from '@/components/ui/Skeleton';
import { createDownloadGrant, createPreviewGrant, type PreviewGrantResult } from '@/lib/api/access';
import { describeError } from '@/lib/api/client';
import type { FileOut } from '@/lib/api/content';
import {
  createReport,
  REPORT_REASON_LABELS,
  type ReportReasonCode,
} from '@/lib/api/moderation';
import {
  createCorrection,
  ratingSummary,
  removeFavorite,
  setFavorite,
  submitRating,
} from '@/lib/api/interaction';
import type { PublicationStatus } from '@/lib/api/types';
import { formatDateTime, formatFileSize } from '@/lib/utils';
import { useAuthStore } from '@/stores/authStore';

const scanLabels: Record<string, { label: string; className: string; downloadable: boolean }> = {
  pending: { label: '等待安全检查', className: 'bg-amber text-on-color', downloadable: false },
  running: { label: '安全检查中', className: 'bg-amber text-on-color', downloadable: false },
  passed: { label: '检查通过', className: 'bg-emerald text-on-color', downloadable: true },
  rejected: { label: '检查未通过', className: 'bg-crimson text-on-color', downloadable: false },
  failed: { label: '检查未完成', className: 'bg-crimson text-on-color', downloadable: false },
};

const purposeLabels: Record<string, string> = {
  primary: '主文件',
  attachment: '附件',
  sample: '示例',
};

function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return `dl-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function ResourceActions({
  resourceId,
  versionId,
  publicationStatus,
  previewAvailable,
  files,
  resourceTitle,
}: {
  resourceId: string;
  versionId: string | null;
  versionNo: number | null;
  publicationStatus: PublicationStatus;
  previewAvailable: boolean;
  files: FileOut[];
  /** 资料标题：用于第一页图片预览的 alt 文案。 */
  resourceTitle?: string;
}) {
  const { status: sessionStatus } = useAuthStore();
  const authed = sessionStatus === 'authenticated';
  const queryClient = useQueryClient();

  const [pendingFileId, setPendingFileId] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewType, setPreviewType] = useState<PreviewGrantResult['preview_type']>('pdf');
  // 多页预览：逐页 URL；没有 pages 字段时退化为单页。
  const [previewPages, setPreviewPages] = useState<NonNullable<PreviewGrantResult['pages']>>([]);
  const [previewPageIndex, setPreviewPageIndex] = useState(0);
  const [previewImageLoading, setPreviewImageLoading] = useState(false);
  const [previewImageFailed, setPreviewImageFailed] = useState(false);
  const downloadKeys = useRef(new Map<string, string>());
  const [favorited, setFavorited] = useState<boolean | null>(null);
  const [favoritePending, setFavoritePending] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const [reportType, setReportType] = useState<'report' | 'correction' | 'classification'>('report');
  const [suggestedCourse, setSuggestedCourse] = useState('');
  const [suggestedType, setSuggestedType] = useState<ResourceType | ''>('');
  const [reportReason, setReportReason] = useState<ReportReasonCode>('copyright');
  const [reportDetail, setReportDetail] = useState('');
  const [myScore, setMyScore] = useState<number | null>(null);

  const ratingQuery = useQuery({
    queryKey: ['rating-summary', resourceId],
    queryFn: () => ratingSummary(resourceId),
  });

  const ratingMutation = useMutation({
    mutationFn: (score: number) => {
      if (!versionId) throw new Error('该资料没有可评价的公开版本');
      return submitRating(resourceId, versionId, score);
    },
    onSuccess: (summary, score) => {
      setMyScore(score);
      queryClient.setQueryData(['rating-summary', resourceId], summary);
      toast.success('评分已保存');
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  const reportMutation = useMutation({
    mutationFn: () => {
      if (reportType !== 'report') {
        if (!versionId) throw new Error('该资料没有可提交纠错的公开版本');
        if (reportType === 'classification' && !suggestedCourse && !suggestedType) throw new Error('请选择建议课程或资料类型');
        return createCorrection({
          resource_id: resourceId,
          version_id: versionId,
          description: reportDetail.trim(),
          suggested_course_id: reportType === 'classification' ? suggestedCourse || undefined : undefined,
          suggested_resource_type: reportType === 'classification' ? suggestedType || undefined : undefined,
        }).then(() => 'correction' as const);
      }
      return createReport({
        resource_id: resourceId,
        version_id: versionId ?? undefined,
        reason_code: reportReason,
        description: reportDetail.trim() || undefined,
      }).then(() => 'report' as const);
    },
    onSuccess: (kind) => {
      setReportOpen(false);
      setReportDetail('');
      setSuggestedCourse('');
      setSuggestedType('');
      toast.success(kind === 'correction' ? '纠错已提交，会进入独立工单处理' : '举报已提交，会进入独立工单处理');
      queryClient.invalidateQueries({ queryKey: [kind === 'report' ? 'my-reports' : 'my-corrections'] });
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  async function handlePreview() {
    if (!versionId) return;
    setPreviewing(true);
    try {
      const grant = await createPreviewGrant(resourceId, versionId);
      const pages =
        grant.pages && grant.pages.length > 0
          ? grant.pages
          : [
              {
                page_no: 1,
                url: grant.preview_url,
                url_expires_at: grant.url_expires_at,
              },
            ];
      setPreviewPages(pages);
      setPreviewPageIndex(0);
      setPreviewUrl(pages[0].url);
      setPreviewType(grant.preview_type ?? 'pdf');
      setPreviewImageLoading(true);
      setPreviewImageFailed(false);
      toast.success(`预览链接有效期至 ${formatDateTime(grant.url_expires_at)}`);
    } catch (error) {
      toast.error(describeError(error).message);
    } finally {
      setPreviewing(false);
    }
  }

  function closePreview() {
    setPreviewUrl(null);
    setPreviewPages([]);
    setPreviewPageIndex(0);
  }

  function goToPreviewPage(next: number) {
    if (next < 0 || next >= previewPages.length) return;
    setPreviewPageIndex(next);
    setPreviewUrl(previewPages[next].url);
    setPreviewImageLoading(true);
    setPreviewImageFailed(false);
  }

  async function handleDownload(file: FileOut) {
    if (!versionId) return;
    setPendingFileId(file.id);
    if (!downloadKeys.current.has(file.id)) downloadKeys.current.set(file.id, newIdempotencyKey());
    try {
      // 幂等键：重试不会重复占用下载额度。
      const grant = await createDownloadGrant(
        resourceId,
        versionId,
        file.id,
        downloadKeys.current.get(file.id)!,
      );
      window.location.assign(grant.download_url);
      toast.success(`已获得下载授权，链接有效期至 ${formatDateTime(grant.url_expires_at)}`);
    } catch (error) {
      if (describeError(error).code === 'GRANT_WINDOW_EXPIRED') downloadKeys.current.delete(file.id);
      toast.error(describeError(error).message);
    } finally {
      setPendingFileId(null);
    }
  }

  async function handleFavorite() {
    setFavoritePending(true);
    try {
      if (favorited) {
        await removeFavorite(resourceId);
        setFavorited(false);
        toast.success('已取消收藏');
      } else {
        await setFavorite(resourceId);
        setFavorited(true);
        toast.success('已收藏');
      }
      queryClient.invalidateQueries({ queryKey: ['favorites'] });
    } catch (error) {
      toast.error(describeError(error).message);
    } finally {
      setFavoritePending(false);
    }
  }

  const summary = ratingQuery.data;
  const published = publicationStatus === 'published';

  return (
    <div className="space-y-6">
      <Modal isOpen={!!previewUrl} onClose={closePreview} title="资料预览">
        {previewUrl && (
          <>
            {previewType === 'image' ? (
              previewImageFailed ? (
                <Notice tone="error" title="预览图片加载失败">
                  短期链接可能已过期，请关闭后重新点击「查看预览」。
                </Notice>
              ) : (
                <div>
                  {previewImageLoading && <Skeleton className="h-[65vh] w-full" />}
                  {/* 多页图片预览：服务端逐页渲染并加水印，浏览器拿到的不是完整 PDF（prevew.md 五）。 */}
                  {/* Signed private URL must bypass the shared image optimizer. */}
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={previewUrl}
                    alt={
                      resourceTitle
                        ? `${resourceTitle} 第 ${previewPageIndex + 1} 页预览`
                        : `资料第 ${previewPageIndex + 1} 页预览`
                    }
                    onLoad={() => setPreviewImageLoading(false)}
                    onError={() => {
                      setPreviewImageLoading(false);
                      setPreviewImageFailed(true);
                    }}
                    className="block max-h-[65vh] w-full object-contain"
                  />
                  {previewPages.length > 1 && (
                    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-divider pt-3">
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => goToPreviewPage(previewPageIndex - 1)}
                        disabled={previewPageIndex <= 0}
                      >
                        上一页
                      </Button>
                      <p className="text-sm text-text-secondary">
                        第 {previewPageIndex + 1} / {previewPages.length} 页（仅提供前 {previewPages.length} 页）
                      </p>
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => goToPreviewPage(previewPageIndex + 1)}
                        disabled={previewPageIndex >= previewPages.length - 1}
                      >
                        下一页
                      </Button>
                    </div>
                  )}
                </div>
              )
            ) : (
              <iframe src={previewUrl} title="获准访问的资料预览" className="h-[65vh] w-full" />
            )}
            <a href={previewUrl} target="_blank" rel="noopener noreferrer" className="mt-3 inline-block underline">在新窗口查看</a>
          </>
        )}
      </Modal>
      {publicationStatus !== 'published' && (
        <Notice tone="warning" title="该资料当前不是已发布状态">
          可能正在审核、已下架或等待处理。此时不能获取新的预览或下载授权。
        </Notice>
      )}

      <div className="flex flex-wrap gap-2">
        <Button
          variant="primary"
          onClick={handlePreview}
          isLoading={previewing}
          disabled={!previewAvailable || !versionId || !published}
        >
          <Eye className="h-4 w-4" aria-hidden="true" />
          查看预览
        </Button>
        {!previewAvailable && (
          <span className="self-center text-xs text-text-secondary">
            该资料暂不提供预览（可能作者已关闭预览，或预览转换尚未完成）。
          </span>
        )}

        {authed ? (
          <Button variant="secondary" onClick={handleFavorite} isLoading={favoritePending}>
            <Heart
              className={`h-4 w-4 ${favorited ? 'fill-crimson text-crimson' : ''}`}
              aria-hidden="true"
            />
            {favorited ? '取消收藏' : '收藏'}
          </Button>
        ) : (
          <a
            href={`/login?next=${encodeURIComponent(`/resources/${resourceId}`)}`}
            className="inline-flex min-h-11 items-center gap-2 border border-control-border px-4 text-sm font-medium text-text-primary hover:bg-canvas"
          >
            <Heart className="h-4 w-4" aria-hidden="true" />
            登录后收藏
          </a>
        )}

        <Button variant="ghost" onClick={() => setReportOpen(true)}>
          <Flag className="h-4 w-4" aria-hidden="true" />
          举报或纠错
        </Button>
      </div>


      {/* 文件清单：检查状态以文字 + 颜色共同表达，未通过检查的文件不可下载。 */}
      <ul className="space-y-3">
        {files.length === 0 && (
          <li>
            <Notice tone="info" title="该版本暂无文件记录">
              文件可能仍在处理中，稍后再查看。
            </Notice>
          </li>
        )}
        {files.map((file) => {
          const scan = scanLabels[file.scan_status] ?? {
            label: file.scan_status,
            className: 'bg-steel text-on-color',
            downloadable: false,
          };
          const canDownload = scan.downloadable && authed && !!versionId && published;
          return (
            <li
              key={file.id}
              className="flex flex-col gap-3 border border-divider bg-surface p-4 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="min-w-0">
                <p className="truncate font-medium text-text-primary">{file.original_name}</p>
                <p className="mt-1 text-xs text-text-secondary">
                  {purposeLabels[file.purpose] ?? file.purpose} · {formatFileSize(file.size)}
                  {file.page_count ? ` · ${file.page_count} 页` : ''}
                </p>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <span className={`px-2 py-0.5 text-xs font-semibold ${scan.className}`}>
                    {scan.label}
                  </span>
                  {!scan.downloadable && (
                    <span className="text-xs text-text-secondary">
                      {file.scan_status === 'pending' || file.scan_status === 'running'
                        ? '安全检查完成前不能下载，可稍后刷新查看进度。'
                        : '该文件未通过安全检查，不能下载。'}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex shrink-0 flex-wrap gap-2">
                {!authed && published && scan.downloadable ? (
                  <a href={`/login?next=${encodeURIComponent(`/resources/${resourceId}`)}`} className="inline-flex min-h-11 items-center bg-brand px-4 text-sm text-on-color">登录后下载</a>
                ) : <Button
                  variant="secondary"
                  onClick={() => handleDownload(file)}
                  isLoading={pendingFileId === file.id}
                  disabled={!canDownload}
                >
                  <Download className="h-4 w-4" aria-hidden="true" />
                  下载原件
                </Button>}
              </div>
            </li>
          );
        })}
      </ul>

      {/* 访问门槛在操作前说明（§26.3）：不把收费/额度留到点击后才发现。 */}
      <details className="border border-divider bg-surface p-3 text-sm text-text-secondary">
        <summary className="cursor-pointer font-medium">访问与下载说明</summary>
        <ul className="space-y-1">
          <li>· 预览与下载由服务端按访问范围、文件状态与下载额度判定。</li>
          <li>· 下载需要登录；未获得权益或超出额度时会明确拒绝，不会出现隐含收费。</li>
          <li>· 已获得的短期链接在有效期内可继续使用；资料下架后不再签发新链接。</li>
        </ul>
      </details>


      {/* 评分：一个用户对同一版本只有一条有效评分，可修改。 */}
      <section aria-labelledby="resource-rating" className="border border-divider bg-surface p-4">
        <h3 id="resource-rating" className="font-semibold text-text-primary">
          评价
        </h3>
        {ratingQuery.error ? (
          <p className="mt-2 text-sm text-text-secondary">
            评分信息暂时无法加载：{describeError(ratingQuery.error).message}
          </p>
        ) : summary && summary.count > 0 ? (
          <p className="mt-2 text-sm text-text-secondary">
            平均 {summary.average.toFixed(1)} 分，共 {summary.count} 人评价
          </p>
        ) : (
          <p className="mt-2 text-sm text-text-secondary">还没有评价。</p>
        )}

        {versionId ? (
          <div className="mt-3 flex items-center gap-2">
            <span className="text-sm text-text-secondary">我的评分：</span>
            {[1, 2, 3, 4, 5].map((score) => (
              <button
                key={score}
                type="button"
                aria-label={`评 ${score} 分`}
                aria-pressed={myScore === score}
                onClick={() => {
                  if (!authed) {
                    toast.error('登录后才可以评价');
                    return;
                  }
                  ratingMutation.mutate(score);
                }}
                className="flex h-11 w-11 items-center justify-center hover:bg-canvas"
              >
                <Star
                  className={`h-5 w-5 ${
                    myScore && score <= myScore ? 'fill-amber text-amber' : 'text-divider'
                  }`}
                  aria-hidden="true"
                />
              </button>
            ))}
            {ratingMutation.isPending && (
              <span className="text-xs text-text-secondary">保存中…</span>
            )}
          </div>
        ) : (
          <p className="mt-3 text-sm text-text-secondary">该资料没有可评价的公开版本。</p>
        )}
      </section>

      <Modal isOpen={reportOpen} onClose={() => setReportOpen(false)} title="举报或纠错">
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!authed) {
              toast.error('登录后才可以提交');
              return;
            }
            if (reportType !== 'report' && !reportDetail.trim()) {
              toast.error('请填写纠错说明');
              return;
            }
            reportMutation.mutate();
          }}
        >
          <div>
            <label htmlFor="report-type" className="mb-1 block text-sm font-medium text-text-primary">
              提交类型
            </label>
            <select
              id="report-type"
              value={reportType}
              onChange={(event) => setReportType(event.target.value as typeof reportType)}
              className="min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
            >
              <option value="report">举报：版权、隐私、安全等问题</option>
              <option value="correction">纠错：内容错误、缺页、版本不符</option>
              <option value="classification">分类错误：课程归属或资料类型不符</option>
            </select>
          </div>
          {reportType === 'classification' && <div className="space-y-3">
            <CoursePicker label="建议课程（可选）" value={suggestedCourse} onChange={setSuggestedCourse} />
            <label className="block text-sm">建议资料类型（可选）<select aria-label="建议资料类型" value={suggestedType} onChange={e => setSuggestedType(e.target.value as ResourceType | '')} className="min-h-11 w-full border border-control-border bg-surface px-3"><option value="">不修改资料类型</option>{RESOURCE_TYPE_ORDER.map(type => <option key={type} value={type}>{RESOURCE_TYPE_LABELS[type]}</option>)}</select></label>
          </div>}
          {reportType === 'report' && (
            <div>
              <label htmlFor="report-reason" className="mb-1 block text-sm font-medium text-text-primary">
                问题类型
              </label>
              <select
                id="report-reason"
                value={reportReason}
                onChange={(event) => setReportReason(event.target.value as ReportReasonCode)}
                className="min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
              >
                {Object.entries(REPORT_REASON_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
          )}
          <div>
            <label htmlFor="report-detail" className="mb-1 block text-sm font-medium text-text-primary">
              {reportType !== 'report' ? '纠错说明（必填）' : '补充说明（可选）'}
            </label>
            <textarea
              id="report-detail"
              value={reportDetail}
              onChange={(event) => setReportDetail(event.target.value)}
              rows={4}
              maxLength={2000}
              className="w-full border border-control-border bg-surface px-3 py-2 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
              placeholder={
                reportType === 'correction'
                  ? '说明错在哪里、正确的是什么；纠错会绑定当前公开版本。'
                  : '说明你发现的问题，便于处理。'
              }
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => setReportOpen(false)}>
              取消
            </Button>
            <Button type="submit" isLoading={reportMutation.isPending}>
              提交
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
