/**
 * 协议与定价接口（backend/app/modules/licensing）。
 *
 * 只读接口 `/me/agreements` 不涉及资金，商业化关闭时仍正常工作并返回空列表；
 * 写接口在开关关闭时抛 NotEnabled（HTTP 503 / FEATURE_NOT_ENABLED）。
 * 金额以整数分表示。取值与 app/core/enums.py 一致。
 */
import { api } from './client';
import type { CursorPage } from './types';

export type AgreementState =
  | 'draft'
  | 'awaiting_acceptance'
  | 'active'
  | 'suspended'
  | 'expired'
  | 'terminated';

export const AGREEMENT_STATE_LABELS: Record<AgreementState, string> = {
  draft: '草稿',
  awaiting_acceptance: '待确认',
  active: '生效中',
  suspended: '已暂停',
  expired: '已过期',
  terminated: '已终止',
};

export interface AgreementVersionOut {
  id: string;
  agreement_id: string;
  version_no: number;
  terms_hash: string;
  rights_scope: Record<string, unknown>;
  territory: string | null;
  starts_at: string | null;
  ends_at: string | null;
  state: AgreementState;
  created_at: string;
  updated_at: string;
}

/** 本人视角的协议及历史版本：只含本人参与的主体，不含他人商业条款与资金字段。 */
export interface AgreementDetailOut {
  id: string;
  platform_party_id: string;
  contributor_party_id: string;
  title: string;
  state: AgreementState;
  created_at: string;
  updated_at: string;
  versions: AgreementVersionOut[];
}

export async function listMyAgreements(): Promise<AgreementDetailOut[]> {
  const { data } = await api.get<AgreementDetailOut[]>('/me/agreements');
  return data;
}

export async function acceptAgreement(version: AgreementVersionOut): Promise<void> {
  await api.post(`/agreements/${version.id}/acceptances`, { accepted_hash: version.terms_hash }, {
    headers: { 'Idempotency-Key': `agreement:${version.id}:${version.terms_hash}` },
  });
}

export interface PriceProposalOut {
  id: string;
  product_id: string;
  contributor_party_id: string | null;
  version_no: number;
  suggested_fen: number;
  final_fen: number | null;
  agreement_version_id: string | null;
  state: 'proposed' | 'negotiating' | 'confirmed' | 'published' | 'withdrawn';
  proposal_hash: string;
  created_at: string;
}

export async function listPriceProposals(admin: boolean, cursor: string | null): Promise<CursorPage<PriceProposalOut>> {
  return (await api.get<CursorPage<PriceProposalOut>>(admin ? '/admin/price-proposals' : '/me/price-proposals', {
    params: { cursor: cursor || undefined },
  })).data;
}

export async function confirmPrice(proposal: PriceProposalOut, admin: boolean): Promise<void> {
  const prefix = admin ? '/admin' : '';
  await api.post(`${prefix}/price-proposals/${proposal.id}/acceptances`, {
    accepted_hash: proposal.proposal_hash,
  }, { headers: { 'Idempotency-Key': `price:${proposal.id}:${proposal.proposal_hash}` } });
}

export async function rejectPrice(proposalId: string, reason: string): Promise<void> {
  await api.post(`/price-proposals/${proposalId}/rejections`, { reason });
}
