import { api } from './client';
import type { CursorPage, ReportStatus } from './types';

export interface PersonalReport {
  id: string;
  kind: 'resource' | 'comment';
  resource_id: string | null;
  version_id: string | null;
  comment_id: string | null;
  available: boolean;
  title: string | null;
  reason_code: string;
  description: string | null;
  status: ReportStatus;
  created_at: string;
  updated_at: string;
}

export interface PersonalReportEvent {
  id: string;
  from_status: ReportStatus | null;
  to_status: ReportStatus;
  created_at: string;
}

export interface PersonalCommentAppeal {
  id: string;
  resource_id: string | null;
  comment_id: string | null;
  available: boolean;
  title: string | null;
  reason: string;
  status: 'pending' | 'accepted' | 'rejected';
  handled_at: string | null;
  created_at: string;
  updated_at: string;
}

export async function listMyReports(params: { kind?: 'resource' | 'comment'; status?: ReportStatus; cursor?: string | null }) {
  return (await api.get<CursorPage<PersonalReport>>('/me/reports', { params: { ...params, cursor: params.cursor || undefined } })).data;
}

export async function listMyReportEvents(id: string, cursor?: string | null) {
  return (await api.get<CursorPage<PersonalReportEvent>>(`/me/reports/${id}/events`, { params: { cursor: cursor || undefined } })).data;
}

export async function listMyCommentAppeals(params: { status?: PersonalCommentAppeal['status']; cursor?: string | null }) {
  return (await api.get<CursorPage<PersonalCommentAppeal>>('/me/comment-appeals', { params: { ...params, cursor: params.cursor || undefined } })).data;
}
