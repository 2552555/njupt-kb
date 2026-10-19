/**
 * 权限分配与资源分配接口（backend/app/modules/assignment）。
 *
 * 管理接口需 `assignment.manage`（角色权限或未过期的按需授权均可满足）。
 * 请求侧 `reason` 必填 1–255 字；取值与 app/core/enums.py 一致。
 */
import { api } from './client';
import type { CursorPage } from './types';

export type AdminPermissionScope = 'global' | 'catalog' | 'resource' | 'finance';

export type AssignmentCapability = 'maintain' | 'review' | 'manage';

export const ADMIN_PERMISSION_SCOPE_LABELS: Record<AdminPermissionScope, string> = {
  global: '全局',
  catalog: '目录',
  resource: '资料',
  finance: '财务',
};

export const ASSIGNMENT_CAPABILITY_LABELS: Record<AssignmentCapability, string> = {
  maintain: '维护',
  review: '审核',
  manage: '管理',
};

/** 权限码与 backend/app/core/permissions.py 的 ALL_PERMISSIONS 一致，供授予表单选择。 */
export const ADMIN_PERMISSION_OPTIONS: { value: string; label: string }[] = [
  { value: 'resource.submit', label: 'resource.submit · 提交资料' },
  { value: 'resource.revise', label: 'resource.revise · 修订资料' },
  { value: 'resource.withdraw', label: 'resource.withdraw · 撤回资料' },
  { value: 'resource.review', label: 'resource.review · 审核资料' },
  { value: 'resource.publish', label: 'resource.publish · 发布资料' },
  { value: 'report.handle', label: 'report.handle · 处理举报' },
  { value: 'correction.handle', label: 'correction.handle · 处理纠错' },
  { value: 'comment.moderate', label: 'comment.moderate · 评论管理' },
  { value: 'request.handle', label: 'request.handle · 处理资料需求' },
  { value: 'catalog.manage', label: 'catalog.manage · 目录维护' },
  { value: 'ops.view', label: 'ops.view · 运营工作台' },
  { value: 'user.suspend', label: 'user.suspend · 账号限制' },
  { value: 'entitlement.grant', label: 'entitlement.grant · 授予权益' },
  { value: 'audit.view', label: 'audit.view · 审计记录' },
  { value: 'job.retry', label: 'job.retry · 重试任务' },
  { value: 'finance.view', label: 'finance.view · 查看财务' },
  { value: 'settlement.manage', label: 'settlement.manage · 结算管理' },
  { value: 'ai.invoke', label: 'ai.invoke · 调用 AI' },
  { value: 'contributor.review', label: 'contributor.review · 入驻审核' },
  { value: 'assignment.manage', label: 'assignment.manage · 权限分配' },
  { value: 'case.handle', label: 'case.handle · 案件处理' },
  { value: 'rights_notice.handle', label: 'rights_notice.handle · 侵权通知处理' },
  { value: 'agreement.manage', label: 'agreement.manage · 协议管理' },
  { value: 'price.confirm', label: 'price.confirm · 价格确认' },
  { value: 'ledger.view', label: 'ledger.view · 查看账本' },
  { value: 'payout.manage', label: 'payout.manage · 付款管理' },
  { value: 'metrics.view', label: 'metrics.view · SLO 指标' },
];

export interface PermissionAssignmentOut {
  id: string;
  user_id: string;
  permission: string;
  scope: AdminPermissionScope;
  scope_catalog_id: string | null;
  scope_resource_id: string | null;
  valid_until: string | null;
  granted_by: string | null;
  reason: string | null;
  revoked_at: string | null;
  revoked_reason: string | null;
  created_at: string;
}

export interface PermissionAssignmentCreate {
  user_id: string;
  permission: string;
  valid_until?: string | null;
  reason: string;
}

export interface ResourceAssignmentOut {
  id: string;
  user_id: string;
  resource_id: string | null;
  course_id: string | null;
  capability: AssignmentCapability;
  effective_at: string | null;
  assigned_by: string | null;
  reason: string | null;
  revoked_at: string | null;
  revoked_reason: string | null;
  created_at: string;
}

export interface ResourceAssignmentCreate {
  user_id: string;
  resource_id?: string | null;
  course_id?: string | null;
  capability?: AssignmentCapability;
  effective_at?: string | null;
  reason: string;
}

export async function listPermissionAssignments(params: {
  user_id?: string;
  cursor?: string | null;
} = {}): Promise<CursorPage<PermissionAssignmentOut>> {
  const { data } = await api.get<CursorPage<PermissionAssignmentOut>>(
    '/admin/permission-assignments',
    { params: { ...params, cursor: params.cursor || undefined } },
  );
  return data;
}

export async function grantPermissionAssignment(
  payload: PermissionAssignmentCreate,
): Promise<PermissionAssignmentOut> {
  const { data } = await api.post<PermissionAssignmentOut>(
    '/admin/permission-assignments',
    payload,
  );
  return data;
}

export async function revokePermissionAssignment(
  assignmentId: string,
  reason: string,
): Promise<PermissionAssignmentOut> {
  const { data } = await api.post<PermissionAssignmentOut>(
    `/admin/permission-assignments/${assignmentId}/revoke`,
    { reason },
  );
  return data;
}

export async function listResourceAssignments(params: {
  user_id?: string;
  cursor?: string | null;
} = {}): Promise<CursorPage<ResourceAssignmentOut>> {
  const { data } = await api.get<CursorPage<ResourceAssignmentOut>>(
    '/admin/resource-assignments',
    { params: { ...params, cursor: params.cursor || undefined } },
  );
  return data;
}

export async function grantResourceAssignment(
  payload: ResourceAssignmentCreate,
): Promise<ResourceAssignmentOut> {
  const { data } = await api.post<ResourceAssignmentOut>('/admin/resource-assignments', payload);
  return data;
}

export async function revokeResourceAssignment(
  assignmentId: string,
  reason: string,
): Promise<ResourceAssignmentOut> {
  const { data } = await api.post<ResourceAssignmentOut>(
    `/admin/resource-assignments/${assignmentId}/revoke`,
    { reason },
  );
  return data;
}
