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

export interface ReportEventOut {
  id: string;
  report_id: string;
  actor_id: string | null;
  from_status: ReportStatus | null;
  to_status: ReportStatus;
  note: string | null;
  evidence: Record<string, unknown> | null;
  created_at: string;
}

export async function listReportEvents(
  reportId: string,
  cursor?: string | null,
): Promise<CursorPage<ReportEventOut>> {
  const { data } = await api.get<CursorPage<ReportEventOut>>(
    `/admin/reports/${reportId}/events`,
    { params: { cursor: cursor || undefined } },
  );
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

// --- 运营任务工作台（backend/app/modules/ops/router.py）---

export interface WorkbenchItem {
  key: string;
  label: string;
  count: number;
  href: string;
}

export interface WorkbenchOut {
  items: WorkbenchItem[];
  total: number;
  generated_at: string;
}

export async function getWorkbench(): Promise<WorkbenchOut> {
  const { data } = await api.get<WorkbenchOut>('/admin/workbench');
  return data;
}

// --- 管理员安全运营视图（backend/app/modules/ops/router.py）---

export interface SecurityLoginFailureItem {
  account: string;
  account_id: string | null;
  count: number;
  last_failed_at: string | null;
}

export interface SecurityNewIpLoginItem {
  account: string;
  account_id: string;
  client_ip: string;
  first_seen_at: string;
}

export interface SecurityOverviewOut {
  login_failures_last_hour: number;
  login_failure_top: SecurityLoginFailureItem[];
  new_ip_login_count: number;
  new_ip_logins: SecurityNewIpLoginItem[];
  download_velocity_flagged_last_7d: number;
  generated_at: string;
}

export async function fetchSecurityOverview(): Promise<SecurityOverviewOut> {
  const { data } = await api.get<SecurityOverviewOut>('/admin/security/overview');
  return data;
}

// --- 只读 SLO 指标（backend/app/modules/ops/router.py，高并发专项 §13）---

export interface DbPoolBudgetOut {
  pool_size: number;
  max_overflow: number;
  pool_timeout_seconds: number;
}

export interface CacheClassStatsOut {
  entries: number;
  hits: number;
  misses: number;
}

export interface CacheStatsOut {
  enabled: boolean;
  entries: number;
  max_entries: number;
  hits: number;
  misses: number;
  evictions: number;
  invalidations: number;
  by_class: Record<string, CacheClassStatsOut>;
}

export interface EventBacklogOut {
  pending: number;
  processing: number;
  processed: number;
  failed: number;
  over_max_attempts: number;
  duplicate_dedupe_keys: number;
}

export interface JobBacklogOut {
  queued: number;
  running: number;
  retry_wait: number;
  succeeded: number;
  failed: number;
  cancelled: number;
  total: number;
}

export interface MetricsOut {
  db_pool: DbPoolBudgetOut;
  cache: CacheStatsOut;
  events: EventBacklogOut;
  jobs: JobBacklogOut;
  slow_query_ms: number;
  generated_at: string;
}

export async function fetchMetrics(): Promise<MetricsOut> {
  const { data } = await api.get<MetricsOut>('/admin/metrics');
  return data;
}

// --- 管理员目录维护（backend/app/modules/catalog/router.py）---

export type CatalogEntityType =
  | 'university'
  | 'college'
  | 'major'
  | 'course'
  | 'teacher'
  | 'semester';

export interface CatalogItem {
  id: string;
  name: string;
  status: string;
  code: string | null;
  short_name: string | null;
  parent_id: string | null;
  parent_name: string | null;
  academic_year: string | null;
  season: string | null;
}

export interface CatalogCreateIn {
  name: string;
  code?: string | null;
  short_name?: string | null;
  slug?: string | null;
  university_id?: string | null;
  college_id?: string | null;
  description?: string | null;
  academic_year?: string | null;
  season?: string | null;
}

export interface CatalogReferenceCount {
  table: string;
  column: string;
  label: string;
  count: number;
}

export interface CatalogMergeImpact {
  entity_type: string;
  source_id: string;
  target_id: string;
  source_name: string;
  target_name: string;
  references: CatalogReferenceCount[];
  total: number;
}

export async function listCatalog(
  entityType: CatalogEntityType,
  params: { q?: string; status?: string } = {},
): Promise<CatalogItem[]> {
  const { data } = await api.get<CatalogItem[]>(`/admin/catalog/${entityType}`, {
    params: { q: params.q || undefined, status: params.status || undefined },
  });
  return data;
}

export async function createCatalog(
  entityType: CatalogEntityType,
  payload: CatalogCreateIn,
): Promise<CatalogItem> {
  const { data } = await api.post<CatalogItem>(
    `/admin/catalog/${entityType}`,
    payload,
  );
  return data;
}

export async function renameCatalog(
  entityType: CatalogEntityType,
  itemId: string,
  name: string,
  reason?: string,
): Promise<CatalogItem> {
  const { data } = await api.patch<CatalogItem>(
    `/admin/catalog/${entityType}/${itemId}`,
    { name, reason: reason ?? null },
  );
  return data;
}

export async function deactivateCatalog(
  entityType: CatalogEntityType,
  itemId: string,
  reason?: string,
): Promise<CatalogItem> {
  const { data } = await api.post<CatalogItem>(
    `/admin/catalog/${entityType}/${itemId}/deactivate`,
    { reason: reason ?? null },
  );
  return data;
}

export async function getCatalogMergeImpact(
  entityType: CatalogEntityType,
  sourceId: string,
  targetId: string,
): Promise<CatalogMergeImpact> {
  const { data } = await api.get<CatalogMergeImpact>(
    `/admin/catalog/${entityType}/merge-impact`,
    { params: { source_id: sourceId, target_id: targetId } },
  );
  return data;
}

export async function mergeCatalog(
  entityType: CatalogEntityType,
  sourceId: string,
  targetId: string,
  reason: string,
): Promise<CatalogMergeImpact> {
  const { data } = await api.post<CatalogMergeImpact>(
    `/admin/catalog/${entityType}/merge`,
    { source_id: sourceId, target_id: targetId, reason },
  );
  return data;
}
