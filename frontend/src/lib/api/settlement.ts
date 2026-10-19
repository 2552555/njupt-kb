import { api } from './client';
import type { CursorPage } from './types';

export interface SettlementItem { id: string; allocation_id: string; amount_fen: number }
export interface SettlementStatement {
  id: string; payee_party_id: string; period: string; currency: string; version: number;
  state: string; gross_fen: number; tax_fen: number; net_fen: number;
  masked_account?: string | null; payout_state?: string | null; items?: SettlementItem[];
}

export async function listStatements(admin: boolean, cursor: string | null): Promise<CursorPage<SettlementStatement>> {
  return (await api.get<CursorPage<SettlementStatement>>(`${admin ? '/admin' : '/me'}/settlement-statements`, {
    params: { cursor: cursor || undefined },
  })).data;
}

export async function confirmStatement(statement: SettlementStatement): Promise<void> {
  await api.post(`/settlement-statements/${statement.id}/confirm`, { expected_version: statement.version });
}

export async function disputeStatement(statement: SettlementStatement, reason: string, items: {
  allocation_id: string; amount_fen: number;
}[]): Promise<void> {
  await api.post(`/settlement-statements/${statement.id}/disputes`, { expected_version: statement.version, reason, items });
}

export async function generateStatement(payee_party_id: string, period: string, currency: string): Promise<void> {
  await api.post('/admin/settlement-statements', { payee_party_id, period, currency });
}

export async function approveStatement(statement: SettlementStatement, account: string): Promise<void> {
  await api.post(`/admin/settlement-statements/${statement.id}/approve`, {
    expected_version: statement.version, payee_snapshot: { account },
  });
}

export interface Payout { id: string; statement_version_id: string; amount_fen: number; state: string }
export interface PayoutAttempt { id: string; payout_id: string; attempt_no: number; state: string; external_ref_key: string | null; evidence_ref: string | null }

export async function listPayouts(cursor: string | null): Promise<CursorPage<Payout>> {
  return (await api.get<CursorPage<Payout>>('/admin/payouts', { params: { cursor: cursor || undefined } })).data;
}
export async function listPayoutAttempts(payoutId: string, cursor: string | null): Promise<CursorPage<PayoutAttempt>> {
  return (await api.get<CursorPage<PayoutAttempt>>(`/admin/payouts/${payoutId}/attempts`, { params: { cursor: cursor || undefined } })).data;
}
export async function createPayoutAttempt(payoutId: string): Promise<void> {
  await api.post(`/admin/payouts/${payoutId}/attempts`, {});
}
export async function reconcilePayoutAttempt(attemptId: string, payload: {
  result: 'succeeded' | 'failed' | 'uncertain'; evidence_ref: string; external_ref_key?: string; reason: string;
}): Promise<void> {
  await api.post(`/admin/payout-attempts/${attemptId}/reconcile`, payload);
}
