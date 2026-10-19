/**
 * 主动防御管理接口（backend/app/modules/defense/router.py）。
 * 默认 observe 模式：只记录与评估，不改变正常请求结果。
 */
import { api } from './client';

export interface DefenseOverview {
  enabled: boolean;
  mode: 'off' | 'observe' | 'enforce';
  policy_source: string;
  events_recent: number;
  subjects_suspicious: number;
  subjects_high: number;
  honeytokens_active: number;
}

export interface DefenseEvent {
  id: string;
  kind: string;
  signal: string;
  weight: number;
  subject_kind: string;
  subject_key: string;
  request_id: string | null;
  method: string | null;
  path_template: string | null;
  decoy_label: string | null;
  detail: Record<string, unknown> | null;
  occurred_at: string;
}

export interface RiskSubject {
  id: string;
  subject_key: string;
  subject_kind: string;
  score: number;
  level: 'normal' | 'suspicious' | 'high';
  action: 'observe' | 'throttle' | 'challenge' | 'review';
  action_expires_at: string | null;
  action_reason: string | null;
  signal_count: number;
  last_signal_at: string | null;
  last_signal_kind: string | null;
  revoked_at: string | null;
  revoke_reason: string | null;
  revision: number;
}

export interface Honeytoken {
  id: string;
  label: string;
  placement: string;
  placement_ref: string | null;
  expected_visitor: string | null;
  status: 'active' | 'retired';
  expires_at: string | null;
  retired_at: string | null;
  note: string | null;
  created_at: string;
}

export interface DefensePolicy {
  id: string;
  version: number;
  mode: 'observe' | 'enforce';
  suspicious_score: number;
  high_score: number;
  throttle_factor_suspicious: number;
  throttle_factor_high: number;
  window_seconds: number;
  decay_half_life_seconds: number;
  action_ttl_seconds: number;
  enabled: boolean;
  valid_from: string | null;
  valid_until: string | null;
  note: string | null;
  created_at: string;
}

export async function defenseOverview(): Promise<DefenseOverview> {
  const { data } = await api.get<DefenseOverview>('/admin/defense/overview');
  return data;
}

export async function defenseEvents(params?: { kind?: string; subject?: string }): Promise<{
  items: DefenseEvent[];
  total: number;
}> {
  const { data } = await api.get<{ items: DefenseEvent[]; total: number }>(
    '/admin/defense/events',
    { params },
  );
  return data;
}

export async function defenseSubjects(params?: { level?: string; action?: string }): Promise<{
  items: RiskSubject[];
  total: number;
}> {
  const { data } = await api.get<{ items: RiskSubject[]; total: number }>(
    '/admin/defense/subjects',
    { params },
  );
  return data;
}

export async function revokeRiskSubject(id: string, reason: string): Promise<RiskSubject> {
  const { data } = await api.post<RiskSubject>(`/admin/defense/subjects/${id}/revoke`, { reason });
  return data;
}

export async function defenseHoneytokens(): Promise<{ items: Honeytoken[]; total: number }> {
  const { data } = await api.get<{ items: Honeytoken[]; total: number }>(
    '/admin/defense/honeytokens',
  );
  return data;
}

export async function createHoneytoken(input: {
  label: string;
  placement: string;
  placement_ref?: string;
  expected_visitor?: string;
  note?: string;
}): Promise<{ token: Honeytoken; raw_token: string; warning: string }> {
  const { data } = await api.post<{ token: Honeytoken; raw_token: string; warning: string }>(
    '/admin/defense/honeytokens',
    input,
  );
  return data;
}

export async function retireHoneytoken(id: string, reason: string): Promise<Honeytoken> {
  const { data } = await api.post<Honeytoken>(`/admin/defense/honeytokens/${id}/retire`, {
    reason,
  });
  return data;
}

export async function defensePolicies(): Promise<{
  items: DefensePolicy[];
  active: {
    mode: string;
    suspicious_score: number;
    high_score: number;
    window_seconds: number;
    source: string;
  };
}> {
  const { data } = await api.get<{
    items: DefensePolicy[];
    active: {
      mode: string;
      suspicious_score: number;
      high_score: number;
      window_seconds: number;
      source: string;
    };
  }>('/admin/defense/policies');
  return data;
}
