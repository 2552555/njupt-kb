import { api } from './client';
import type { CursorPage } from './types';

export interface PersonalOverview {
  resources: number; published: number; drafts: number; pending_reviews: number;
  favorites: number; follows: number; unread_notifications: number;
  open_requests: number; open_corrections: number; downloads: number;
  received_download_grants: number; received_ratings: number; average_rating: number | null;
}
export interface DownloadHistoryItem {
  id: string; created_at: string; status: string; available: boolean;
  resource_id: string | null; title: string | null; file_name: string | null; version_no: number | null;
}
export interface FeedItem {
  id: string; title: string; resource_type: string; course_id: string; course_name: string;
  owner_id: string; version_no: number; updated_at: string;
}
export interface ManagedUser {
  id: string; email: string; display_name: string; role: string; status: string;
  email_verified: boolean; created_at: string;
}
export interface ManagedJob {
  id: string; job_type: string; target_id: string; status: string; attempts: number;
  max_attempts: number; last_error: string | null; failure_class: string | null;
  next_run_at: string; created_at: string; updated_at: string;
}
export interface AuditItem {
  id: string; actor_id: string | null; action: string; object_type: string;
  object_id: string | null; before_state: Record<string, unknown> | null;
  after_state: Record<string, unknown> | null; reason: string | null;
  request_id: string | null; created_at: string;
}
export interface EntitlementItem {
  id: string; user_id: string; scope_id: string; scope_type: string; capability: string;
  source_type: string; starts_at: string | null; expires_at: string | null;
  revoked_at: string | null; revoked_reason: string | null; created_at: string;
}
export async function getPersonalOverview() {
  return (await api.get<PersonalOverview>('/me/overview')).data;
}
export async function getWorkspacePage<T>(path: string, params: Record<string, string>, cursor: string | null) {
  const filters = Object.fromEntries(Object.entries(params).filter(([, value]) => value !== ''));
  return (await api.get<CursorPage<T>>(path, { params: { ...filters, cursor: cursor || undefined, limit: 20 } })).data;
}
