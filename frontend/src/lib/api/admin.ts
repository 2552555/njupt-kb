/**
 * 管理端接口（moderation / access 的管理路由；jobs 模块尚未落地，暂不封装）。
 * 全部要求相应权限码；前端按角色隐藏入口，但权限最终由服务端判定（§5.5）。
 */
import { api } from './client';
import type { EntitlementOut } from './access';
import type { ReportOut } from './moderation';
import type {
  Capability,
  CursorPage,
  EntitlementSourceType,
  FreshnessStatus,
  PublicationStatus,
  ReportStatus,
  ScopeType,
} from './types';

export type PublicationAction = 'publish' | 'restore' | 'rollback';

export interface PublicationIn {
  action: PublicationAction;
  version_id?: string | null;
  expected_revision: number;
  reason: string;
}

export interface PublicationResult {
  resource_id: string;
  version_id: string;
  action: PublicationAction;
  publication_status: PublicationStatus;
}

export interface AuditLogOut {
  id: string;
  actor_id: string | null;
  action: string;
  object_type: string;
  object_id: string | null;
  object_version: string | null;
  before_state: Record<string, unknown> | null;
  after_state: Record<string, unknown> | null;
  reason: string | null;
  request_id: string | null;
  created_at: string;
}

export interface ModerationSummaryOut {
  reviews: Record<string, number>;
  reports: Record<string, number>;
  oldest_pending_submitted_at: string | null;
  generated_at: string;
}

export interface ReportEventIn {
  to_status: ReportStatus;
  note?: string | null;
  evidence?: Record<string, unknown> | null;
  interim_action?: 'none' | 'restrict_access' | 'release_access';
}

export interface AccessPolicyOut {
  resource_id: string;
  metadata_scope: ScopeType;
  preview_scope: ScopeType;
  original_scope: ScopeType;
  revision: number;
  updated_at: string;
}

export interface AccessPolicyUpdate {
  metadata_scope: ScopeType;
  preview_scope: ScopeType;
  original_scope: ScopeType;
}

export interface EntitlementCreate {
  user_id: string;
  scope_type: ScopeType;
  scope_id: string;
  capability: Capability;
  source_type: EntitlementSourceType;
  source_id?: string | null;
  starts_at?: string | null;
  expires_at?: string | null;
}

// --- 发布与时效 ---

export async function setPublication(
  resourceId: string,
  payload: PublicationIn,
): Promise<PublicationResult> {
  const { data } = await api.post<PublicationResult>(
    `/admin/resources/${resourceId}/publication`,
    payload,
  );
  return data;
}

export async function setFreshness(
  resourceId: string,
  status: FreshnessStatus,
  reason?: string,
): Promise<void> {
  await api.post(`/admin/resources/${resourceId}/freshness`, { status, reason });
}

// --- 举报工单 ---

export async function listReports(params: {
  status?: ReportStatus[];
  resource_id?: string;
  cursor?: string | null;
  limit?: number;
} = {}): Promise<CursorPage<ReportOut>> {
  const { data } = await api.get<CursorPage<ReportOut>>('/admin/reports', {
    params: { ...params, cursor: params.cursor || undefined },
  });
  return data;
}

export async function appendReportEvent(
  reportId: string,
  payload: ReportEventIn,
): Promise<void> {
  await api.post(`/admin/reports/${reportId}/events`, payload);
}

// --- 审计与概览 ---

export async function listAuditLogs(params: {
  object_type?: string;
  object_id?: string;
  action?: string;
  cursor?: string | null;
} = {}): Promise<CursorPage<AuditLogOut>> {
  const { data } = await api.get<CursorPage<AuditLogOut>>('/admin/audit', {
    params: { ...params, cursor: params.cursor || undefined },
  });
  return data;
}

export async function moderationSummary(): Promise<ModerationSummaryOut> {
  const { data } = await api.get<ModerationSummaryOut>('/admin/moderation/summary');
  return data;
}

// --- 目录维护与账号限制 ---

export async function correctResourceCourse(
  resourceId: string,
  courseId: string,
  reason: string,
): Promise<void> {
  await api.post(`/admin/catalog/resources/${resourceId}/course`, {
    course_id: courseId,
    reason,
  });
}

export async function suspendUser(
  userId: string,
  reason: string,
  until?: string | null,
): Promise<void> {
  await api.post(`/admin/users/${userId}/suspend`, { reason, until: until ?? null });
}

export async function reinstateUser(userId: string, reason?: string): Promise<void> {
  await api.post(`/admin/users/${userId}/reinstate`, { reason: reason ?? null });
}

// --- 权益与访问策略 ---

export async function listEntitlements(params: {
  user_id?: string;
  cursor?: string | null;
} = {}): Promise<CursorPage<EntitlementOut>> {
  const { data } = await api.get<CursorPage<EntitlementOut>>('/admin/entitlements', {
    params: { ...params, cursor: params.cursor || undefined },
  });
  return data;
}

export async function createEntitlement(
  payload: EntitlementCreate,
): Promise<EntitlementOut> {
  const { data } = await api.post<EntitlementOut>('/admin/entitlements', payload);
  return data;
}

export async function revokeEntitlement(
  entitlementId: string,
  reason: string,
): Promise<EntitlementOut> {
  const { data } = await api.post<EntitlementOut>(
    `/admin/entitlements/${entitlementId}/revoke`,
    { reason },
  );
  return data;
}

export async function setAccessPolicy(
  resourceId: string,
  payload: AccessPolicyUpdate,
): Promise<AccessPolicyOut> {
  const { data } = await api.put<AccessPolicyOut>(
    `/admin/resources/${resourceId}/access-policy`,
    payload,
  );
  return data;
}
