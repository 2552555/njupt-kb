/**
 * 案件、侵权通知与反通知接口（backend/app/modules/cases）。
 *
 * 分层返回：本人视角不返回内部备注、证据与处理人，处理人视角才有这些字段。
 * 因此本人视角的类型只声明对外字段，不保留内部字段占位（前端不展示也不请求）。
 * 取值与 app/core/enums.py 一致。
 */
import { api } from './client';
import type { CursorPage } from './types';

export type CaseType =
  | 'delivery_dispute'
  | 'revenue_dispute'
  | 'rights_notice'
  | 'general_appeal';

export type CaseStatus = 'open' | 'triaged' | 'investigating' | 'decided' | 'closed';

export type CaseSubjectType =
  | 'resource'
  | 'resource_version'
  | 'comment'
  | 'order'
  | 'order_item'
  | 'other';

export type CaseEventType =
  | 'submitted'
  | 'triaged'
  | 'forwarded'
  | 'counter_notice'
  | 'measure_taken'
  | 'measure_released'
  | 'decided'
  | 'closed'
  | 'note';

export type CaseMeasureKind =
  | 'unpublish'
  | 'block_download'
  | 'block_sales'
  | 'restrict_preview'
  | 'freeze_revenue';

export type CaseMeasureStatus = 'active' | 'released';

export type RightsNoticeStatus =
  | 'received'
  | 'needs_info'
  | 'forwarded'
  | 'counter_received'
  | 'measured'
  | 'closed';

export const CASE_TYPE_ORDER: CaseType[] = [
  'delivery_dispute',
  'revenue_dispute',
  'rights_notice',
  'general_appeal',
];

export const CASE_TYPE_LABELS: Record<CaseType, string> = {
  delivery_dispute: '交付争议',
  revenue_dispute: '收益争议',
  rights_notice: '侵权通知',
  general_appeal: '一般申诉',
};

export const CASE_STATUS_LABELS: Record<CaseStatus, string> = {
  open: '待受理',
  triaged: '已受理',
  investigating: '调查中',
  decided: '已决定',
  closed: '已关闭',
};

export const CASE_SUBJECT_TYPE_LABELS: Record<CaseSubjectType, string> = {
  resource: '资料',
  resource_version: '资料版本',
  comment: '评论',
  order: '订单',
  order_item: '订单条目',
  other: '其他',
};

export const CASE_EVENT_TYPE_LABELS: Record<CaseEventType, string> = {
  submitted: '已提交',
  triaged: '已受理',
  forwarded: '已转送',
  counter_notice: '收到反通知',
  measure_taken: '已采取措施',
  measure_released: '已解除措施',
  decided: '已决定',
  closed: '已关闭',
  note: '过程记录',
};

export const CASE_MEASURE_KIND_LABELS: Record<CaseMeasureKind, string> = {
  unpublish: '撤下内容',
  block_download: '停止下载',
  block_sales: '停止销售',
  restrict_preview: '限制预览',
  freeze_revenue: '冻结收益',
};

export const CASE_MEASURE_STATUS_LABELS: Record<CaseMeasureStatus, string> = {
  active: '生效中',
  released: '已解除',
};

export const RIGHTS_NOTICE_STATUS_LABELS: Record<RightsNoticeStatus, string> = {
  received: '已受理',
  needs_info: '需补充材料',
  forwarded: '已转送',
  counter_received: '已收到反通知',
  measured: '已采取措施',
  closed: '已关闭',
};

/** 本人视角的案件列表项（不含内部字段）。 */
export interface CaseListItemOut {
  id: string;
  case_type: CaseType;
  subject_type: CaseSubjectType;
  subject_id: string;
  state: CaseStatus;
  title: string;
  created_at: string;
  updated_at: string;
}

/**
 * 案件时间线事件（本人视角）。
 * 后端 `note`/`evidence_ref`/`actor_id` 只在处理人视角返回，本人视角不请求也不展示。
 */
export interface CaseEventOut {
  id: string;
  event_type: CaseEventType;
  served_at: string | null;
  deadline_at: string | null;
  created_at: string;
}

/** 本人视角的案件详情：含本人理由与对外结论。 */
export interface CaseDetailOut {
  id: string;
  case_type: CaseType;
  subject_type: CaseSubjectType;
  subject_id: string;
  state: CaseStatus;
  title: string;
  description?: string | null;
  rule_version?: string | null;
  decided_at?: string | null;
  decision_reason?: string | null;
  closed_at?: string | null;
  created_at: string;
  updated_at: string;
  events?: CaseEventOut[];
}

/** 处理人视角的案件（后台队列）。 */
export interface CaseAdminOut {
  id: string;
  case_type: CaseType;
  subject_type: CaseSubjectType;
  subject_id: string;
  applicant_id: string;
  state: CaseStatus;
  rule_version: string | null;
  handler_id: string | null;
  title: string;
  description: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  closed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface RightsNoticeCreateIn {
  claimant_name: string;
  claimant_contact: string;
  rights_basis_ref: string;
  target_type: CaseSubjectType;
  target_id: string;
  content_ref: string;
  requested_action: string;
  statement_version: string;
  evidence_ref: string;
  truthfulness_declared: boolean;
}

export interface RightsNoticeReceiptOut {
  notice_id: string;
  case_id: string;
  status: RightsNoticeStatus;
}

export interface MeasureCreateIn {
  measure: CaseMeasureKind;
  target_type: CaseSubjectType;
  target_id: string;
  reason: string;
  amount_fen?: number | null;
}

export interface MeasureOut {
  id: string;
  case_id: string;
  target_type: CaseSubjectType;
  target_id: string;
  measure: CaseMeasureKind;
  status: CaseMeasureStatus;
  amount_fen: number | null;
  effective_at: string | null;
  released_at: string | null;
  reason: string | null;
  actor_id: string | null;
  created_at: string;
  updated_at: string;
  /** 商业化关闭时 BLOCK_SALES／FREEZE_REVENUE 只记录措施行，不实际作用资金。 */
  effect_applied: boolean;
}

export interface CaseEventIn {
  event_type: CaseEventType;
  reason: string;
  note?: string | null;
  evidence_ref?: string | null;
  served_at?: string | null;
  deadline_at?: string | null;
}

export async function listMyCases(params: {
  case_type?: CaseType[];
  state?: CaseStatus[];
  cursor?: string | null;
} = {}): Promise<CursorPage<CaseListItemOut>> {
  const { data } = await api.get<CursorPage<CaseListItemOut>>('/me/cases', {
    params: { ...params, cursor: params.cursor || undefined },
  });
  return data;
}

export async function getMyCase(caseId: string): Promise<CaseDetailOut> {
  const { data } = await api.get<CaseDetailOut>(`/me/cases/${caseId}`);
  return data;
}

export async function createRightsNotice(
  payload: RightsNoticeCreateIn,
): Promise<RightsNoticeReceiptOut> {
  const { data } = await api.post<RightsNoticeReceiptOut>('/rights-notices', payload);
  return data;
}

export async function listAdminCases(params: {
  case_type?: CaseType[];
  state?: CaseStatus[];
  cursor?: string | null;
} = {}): Promise<CursorPage<CaseAdminOut>> {
  const { data } = await api.get<CursorPage<CaseAdminOut>>('/admin/cases', {
    params: { ...params, cursor: params.cursor || undefined },
  });
  return data;
}

export async function appendCaseEvent(
  caseId: string,
  payload: CaseEventIn,
): Promise<void> {
  await api.post(`/admin/cases/${caseId}/events`, payload);
}

export async function takeMeasure(caseId: string, payload: MeasureCreateIn): Promise<MeasureOut> {
  const { data } = await api.post<MeasureOut>(`/admin/cases/${caseId}/measures`, payload);
  return data;
}

export async function releaseMeasure(caseId: string, measureId: string): Promise<MeasureOut> {
  const { data } = await api.post<MeasureOut>(
    `/admin/cases/${caseId}/measures/${measureId}/release`,
  );
  return data;
}
