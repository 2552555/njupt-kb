/**
 * 审核、举报与审核队列接口（backend/app/modules/moderation/router.py）。
 * 审核决定不可原地覆盖；并发时过期快照返回 409（§13.3）。
 */
import { api } from './client';
import type {
  CursorPage,
  ModerationDecisionType,
  ReviewStatus,
} from './types';

/** §18 投诉理由分类：有限分类 + 描述，不用自由文本做工单键。 */
export type ReportReasonCode =
  | 'copyright'
  | 'privacy'
  | 'inappropriate'
  | 'misinformation'
  | 'spam'
  | 'malware'
  | 'other';

export const REPORT_REASON_LABELS: Record<ReportReasonCode, string> = {
  copyright: '版权问题',
  privacy: '隐私信息',
  inappropriate: '不适宜内容',
  misinformation: '内容错误或误导',
  spam: '垃圾信息',
  malware: '可疑或恶意文件',
  other: '其他',
};

export interface ReviewOut {
  id: string;
  resource_id: string;
  version_id: string;
  submitter_id: string;
  status: ReviewStatus;
  snapshot_revision: number;
  assigned_reviewer_id: string | null;
  submitted_at: string;
  decided_at: string | null;
  created_at: string;
  updated_at: string;
  /** 当前请求者是否可对该任务下决定（作者不能审核自己的提交）。 */
  can_decide: boolean | null;
}

export interface ReviewDecisionIn {
  decision: ModerationDecisionType;
  reason: string;
  snapshot_revision: number;
}

export interface ModerationDecisionOut {
  id: string;
  review_id: string;
  version_id: string;
  reviewer_id: string;
  decision: ModerationDecisionType;
  reason: string;
  snapshot_revision: number;
  created_at: string;
}

export interface ReportCreateIn {
  resource_id: string;
  version_id?: string | null;
  reason_code: ReportReasonCode;
  description?: string | null;
}

export interface ReportOut {
  id: string;
  resource_id: string;
  version_id: string | null;
  reporter_id: string;
  reason_code: string;
  description: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface ReviewListParams {
  status?: ReviewStatus[];
  resource_id?: string;
  version_id?: string;
  submitted_from?: string;
  submitted_to?: string;
  cursor?: string | null;
  limit?: number;
}

export async function createReport(payload: ReportCreateIn): Promise<ReportOut> {
  const { data } = await api.post<ReportOut>('/reports', payload);
  return data;
}

export async function listReviews(
  params: ReviewListParams = {},
): Promise<CursorPage<ReviewOut>> {
  const { data } = await api.get<CursorPage<ReviewOut>>('/admin/reviews', {
    params: {
      status: params.status,
      resource_id: params.resource_id,
      version_id: params.version_id,
      submitted_from: params.submitted_from,
      submitted_to: params.submitted_to,
      cursor: params.cursor || undefined,
      limit: params.limit,
    },
  });
  return data;
}

export async function decideReview(
  reviewId: string,
  payload: ReviewDecisionIn,
): Promise<ModerationDecisionOut> {
  const { data } = await api.post<ModerationDecisionOut>(
    `/admin/reviews/${reviewId}/decision`,
    payload,
  );
  return data;
}
