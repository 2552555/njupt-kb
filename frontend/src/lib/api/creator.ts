import { api } from './client';
import type { CursorPage } from './types';

/** 贡献记录类型（§24.1）：只接受事实型贡献，不含访问/下载等使用行为。 */
export type ContributionKind =
  | 'published_resource'
  | 'accepted_revision'
  | 'adopted_correction'
  | 'resolved_request';

export const CONTRIBUTION_KIND_LABELS: Record<ContributionKind, string> = {
  published_resource: '发布资料',
  accepted_revision: '修订被采纳',
  adopted_correction: '纠错被采纳',
  resolved_request: '解决资料需求',
};

export interface ContributionSummary {
  user_id: string;
  total: number;
  by_kind: Partial<Record<ContributionKind, number>>;
  /** 展示用贡献分，不承诺兑换现金。 */
  points: number;
  is_creator: boolean;
}

export interface ContributionOut {
  id: string;
  user_id: string;
  kind: ContributionKind;
  reference_type: string;
  reference_id: string;
  note: string | null;
  points: number;
  revoked_at: string | null;
  created_at: string;
}

export interface CreatorProfileOut {
  user_id: string;
  display_name: string;
  headline: string | null;
  bio: string | null;
  avatar_url: string | null;
  contribution: ContributionSummary;
}

/** 本人视角：额外返回是否公开，且未公开的简介也如实返回，便于回填表单。 */
export interface CreatorSelfProfileOut extends CreatorProfileOut {
  is_public: boolean;
}

export interface CreatorProfileUpdate {
  headline?: string | null;
  bio?: string | null;
  is_public?: boolean;
}

export async function getCreatorProfile(userId: string): Promise<CreatorProfileOut> {
  const { data } = await api.get<CreatorProfileOut>(`/creators/${userId}`);
  return data;
}

export async function getMyCreatorProfile(): Promise<CreatorSelfProfileOut> {
  const { data } = await api.get<CreatorSelfProfileOut>('/me/creator-profile');
  return data;
}

export async function updateMyCreatorProfile(
  payload: CreatorProfileUpdate,
): Promise<CreatorSelfProfileOut> {
  const { data } = await api.put<CreatorSelfProfileOut>('/me/creator-profile', payload);
  return data;
}

export async function listMyContributions(
  cursor?: string | null,
): Promise<CursorPage<ContributionOut>> {
  const { data } = await api.get<CursorPage<ContributionOut>>('/me/contributions', {
    params: { cursor: cursor || undefined },
  });
  return data;
}

// --- 贡献者入驻（§3.4；backend/app/modules/creator）---

/** 贡献者入驻状态机；降级不抹去已生效授权与已得收益。 */
export type ContributorOnboardingStatus =
  | 'applied'
  | 'under_review'
  | 'active'
  | 'rejected'
  | 'suspended'
  | 'exited';

export const CONTRIBUTOR_ONBOARDING_STATUS_LABELS: Record<ContributorOnboardingStatus, string> = {
  applied: '已提交',
  under_review: '审核中',
  active: '已入驻',
  rejected: '未通过',
  suspended: '已暂停',
  exited: '已退出',
};

export interface ContributorApplicationOut {
  id: string;
  user_id: string;
  status: ContributorOnboardingStatus;
  capability_summary: string;
  rights_materials_ref: string;
  verification_ref: string | null;
  contract_party_ref: string | null;
  submitted_at: string;
  decided_at: string | null;
  decided_by: string | null;
  decision_reason: string | null;
  closed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ContributorApplicationCreate {
  capability_summary: string;
  rights_materials_ref: string;
  verification_ref?: string | null;
  contract_party_ref?: string | null;
}

/** 审核决定：受理（转审核中）、通过入驻、拒绝。理由必填。 */
export type ContributorDecision = 'under_review' | 'active' | 'rejected';

export const CONTRIBUTOR_DECISION_LABELS: Record<ContributorDecision, string> = {
  under_review: '受理（转审核中）',
  active: '通过入驻',
  rejected: '拒绝',
};

export interface ContributorApplicationDecisionIn {
  decision: ContributorDecision;
  reason: string;
}

export async function submitContributorApplication(
  payload: ContributorApplicationCreate,
): Promise<ContributorApplicationOut> {
  const { data } = await api.post<ContributorApplicationOut>(
    '/me/contributor-applications',
    payload,
  );
  return data;
}

export async function listMyContributorApplications(
  cursor?: string | null,
): Promise<CursorPage<ContributorApplicationOut>> {
  const { data } = await api.get<CursorPage<ContributorApplicationOut>>(
    '/me/contributor-applications',
    { params: { cursor: cursor || undefined } },
  );
  return data;
}

export async function listContributorApplications(params: {
  status?: ContributorOnboardingStatus[];
  cursor?: string | null;
} = {}): Promise<CursorPage<ContributorApplicationOut>> {
  const { data } = await api.get<CursorPage<ContributorApplicationOut>>(
    '/admin/contributor-applications',
    { params: { ...params, cursor: params.cursor || undefined } },
  );
  return data;
}

export async function decideContributorApplication(
  applicationId: string,
  payload: ContributorApplicationDecisionIn,
): Promise<ContributorApplicationOut> {
  const { data } = await api.post<ContributorApplicationOut>(
    `/admin/contributor-applications/${applicationId}/decisions`,
    payload,
  );
  return data;
}

export async function changeContributorStatus(
  application: ContributorApplicationOut,
  status: 'active' | 'suspended' | 'exited',
  reason: string,
  selfExit = false,
): Promise<ContributorApplicationOut> {
  const path = selfExit
    ? `/me/contributor-applications/${application.id}/exit`
    : `/admin/contributor-applications/${application.id}/status`;
  const { data } = await api.post<ContributorApplicationOut>(path, {
    expected_status: application.status, status, reason,
  });
  return data;
}
