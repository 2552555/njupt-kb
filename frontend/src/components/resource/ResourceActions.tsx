'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Download, Eye, Flag, Heart, Star } from 'lucide-react';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { createDownloadGrant, createPreviewGrant } from '@/lib/api/access';
import { describeError } from '@/lib/api/client';
import type { FileOut } from '@/lib/api/content';
import {
  createReport,
  REPORT_REASON_LABELS,
  type ReportReasonCode,
} from '@/lib/api/moderation';
import {
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
  versionNo,
  publicationStatus,
  previewAvailable,
  files,
}: {
  resourceId: string;
  versionId: string | null;
  versionNo: number | null;
  publicationStatus: PublicationStatus;
  previewAvailable: boolean;
  files: FileOut[];
}) {
  const { status: sessionStatus } = useAuthStore();
  const authed = sessionStatus === 'authenticated';
  const queryClient = useQueryClient();

  const [pendingFileId, setPendingFileId] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [favorited, setFavorited] = useState<boolean | null>(null);
  const [favoritePending, setFavoritePending] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
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
    mutationFn: () =>
      createReport({
        resource_id: resourceId,
        version_id: versionId ?? undefined,
        reason_code: reportReason,
        description: reportDetail.trim() || undefined,
      }),
    onSuccess: () => {
      setReportOpen(false);
      setReportDetail('');
      toast.success('举报已提交，会进入独立工单处理');
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  async function handlePreview() {
    if (!versionId) return;
    setPreviewing(true);
    try {
      const grant = await createPreviewGrant(resourceId, versionId);
      window.open(grant.preview_url, '_blank', 'noopener,noreferrer');
      toast.success(`预览链接有效期至 ${formatDateTime(grant.url_expires_at)}`);
    } catch (error) {
      toast.error(describeError(error).message);
    } finally {
      setPreviewing(false);
    }
  }

  async function handleDownload(file: FileOut) {
    if (!versionId) return;
    setPendingFileId(file.id);
    try {
      // 幂等键：重试不会重复占用下载额度。
      const grant = await createDownloadGrant(
        resourceId,
        versionId,
        file.id,
        newIdempotencyKey(),
      );
      window.location.assign(grant.download_url);
      toast.success(`已获得下载授权，链接有效期至 ${formatDateTime(grant.url_expires_at)}`);
    } catch (error) {
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

  return (
    <div className="space-y-6">
      {publicationStatus !== 'published' && (
        <Notice tone="warning" title="该资料当前不是已发布状态">
          可能正在审核、已下架或等待处理。此时不能获取新的预览或下载授权。
        </Notice>
      )}

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
          const canDownload = scan.downloadable && authed && !!versionId;
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
                <Button
                  variant="secondary"
                  onClick={() => handleDownload(file)}
                  isLoading={pendingFileId === file.id}
                  disabled={!canDownload}
                >
                  <Download className="h-4 w-4" aria-hidden="true" />
                  下载原件
                </Button>
              </div>
            </li>
          );
        })}
      </ul>

      {/* 访问门槛在操作前说明（§26.3）：不把收费/额度留到点击后才发现。 */}
      <Notice tone="info" title="访问说明">
        <ul className="space-y-1">
          <li>· 预览与下载由服务端按访问范围、文件状态与下载额度判定。</li>
          <li>· 下载需要登录；未获得权益或超出额度时会明确拒绝，不会出现隐含收费。</li>
          <li>· 已获得的短期链接在有效期内可继续使用；资料下架后不再签发新链接。</li>
        </ul>
      </Notice>

      <div className="flex flex-wrap gap-2">
        <Button
          variant="primary"
          onClick={handlePreview}
          isLoading={previewing}
          disabled={!previewAvailable || !versionId}
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
        <p className="mt-2 text-xs text-text-secondary">
          评价资格需先获得该内容的访问权，避免未接触内容的请求影响评分。
        </p>
      </section>

      <Modal isOpen={reportOpen} onClose={() => setReportOpen(false)} title="举报或纠错">
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!authed) {
              toast.error('登录后才可以举报');
              return;
            }
            reportMutation.mutate();
          }}
        >
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
          <div>
            <label htmlFor="report-detail" className="mb-1 block text-sm font-medium text-text-primary">
              补充说明（可选）
            </label>
            <textarea
              id="report-detail"
              value={reportDetail}
              onChange={(event) => setReportDetail(event.target.value)}
              rows={4}
              maxLength={2000}
              className="w-full border border-control-border bg-surface px-3 py-2 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
              placeholder="说明你发现的问题，便于处理。"
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
