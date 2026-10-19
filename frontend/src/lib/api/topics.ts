/**
 * 保研 / 考研 / 竞赛专题接口（backend/app/modules/topics/router.py）。
 * 这些专题使用独立的数据模型，不与课程实体混用；来源与年份是必读字段。
 */
import { api } from './client';
import type { CursorPage } from './types';

export interface BaoyanPolicy {
  id: string;
  college_id: string;
  year: number;
  gpa_min: number | null;
  gpa_rank_requirement: string | null;
  comprehensive_formula: string | null;
  total_quota: number | null;
  regular_quota: number | null;
  special_quota: number | null;
  competition_bonus: Record<string, unknown> | null;
  research_bonus: Record<string, unknown> | null;
  timeline: Record<string, unknown> | null;
  source_url: string | null;
  source_type: string | null;
  is_verified: boolean;
  created_at: string;
}

export interface BaoyanExperience {
  id: string;
  author_id: string;
  college_id: string;
  major_id: string;
  original_major: string | null;
  gpa: number | null;
  gpa_rank: string | null;
  target_university: string;
  target_college: string | null;
  target_major: string | null;
  offer_result: string | null;
  timeline: Record<string, unknown> | null;
  summer_camp_experience: string | null;
  pre_interview_experience: string | null;
  interview_questions: Record<string, unknown> | null;
  advice: string | null;
  year: number;
  tags: unknown;
  view_count: number;
  helpful_count: number;
  status: string;
  created_at: string;
}

export interface Competition {
  id: string;
  name: string;
  category: string | null;
  difficulty: number | null;
  organizer: string | null;
  official_url: string | null;
  description: string | null;
  registration_period: string | null;
  competition_period: string | null;
  suitable_majors: unknown;
  suitable_grades: unknown;
  team_size: string | null;
  njupt_contact: string | null;
  njupt_winners_count: number | null;
  created_at: string;
  winners?: CompetitionWinner[];
}

export interface CompetitionWinner {
  id: string;
  competition_id: string;
  year: number;
  award_level: string;
  team_members: unknown;
  advisor: string | null;
  project_title: string | null;
  experience_text: string | null;
  created_at: string;
}

export interface KaoyanSchool {
  id: string;
  name: string;
  is_njupt: boolean;
  website_url: string | null;
  created_at: string;
}

export interface KaoyanMajor {
  id: string;
  school_id: string;
  name: string;
  college: string | null;
  exam_subjects: Record<string, unknown> | null;
  reference_books: Record<string, unknown> | null;
  created_at: string;
}

export interface KaoyanScoreLine {
  id: string;
  kaoyan_major_id: string;
  year: number;
  total_score_line: number | null;
  political_line: number | null;
  english_line: number | null;
  business1_line: number | null;
  business2_line: number | null;
  applicants: number | null;
  admitted: number | null;
  lowest_score: number | null;
  highest_score: number | null;
  avg_score: number | null;
  source_url: string | null;
  created_at: string;
}

export interface CollegeBrief {
  id: string;
  name: string;
  slug?: string;
}

export interface MajorBrief {
  id: string;
  name: string;
  college_id?: string;
}

export async function listBaoyanPolicies(params?: {
  college_id?: string;
  year?: number;
  limit?: number;
}): Promise<CursorPage<BaoyanPolicy>> {
  const { data } = await api.get<CursorPage<BaoyanPolicy>>('/baoyan/policies', {
    params: { limit: 20, ...params },
  });
  return data;
}

export async function listBaoyanExperiences(params?: {
  college_id?: string;
  target_university?: string;
  year?: number;
  limit?: number;
}): Promise<CursorPage<BaoyanExperience>> {
  const { data } = await api.get<CursorPage<BaoyanExperience>>('/baoyan/experiences', {
    params: { limit: 12, ...params },
  });
  return data;
}

export async function getBaoyanExperience(id: string): Promise<BaoyanExperience> {
  const { data } = await api.get<BaoyanExperience>(`/baoyan/experiences/${id}`);
  return data;
}

export async function createBaoyanExperience(input: {
  college_id: string;
  major_id: string;
  original_major?: string;
  gpa?: number;
  gpa_rank?: string;
  target_university: string;
  target_college?: string;
  target_major?: string;
  offer_result?: string;
  summer_camp_experience?: string;
  pre_interview_experience?: string;
  advice?: string;
  year: number;
}): Promise<BaoyanExperience> {
  const { data } = await api.post<BaoyanExperience>('/baoyan/experiences', input);
  return data;
}

export async function listCompetitions(params?: {
  category?: string;
  limit?: number;
}): Promise<CursorPage<Competition>> {
  const { data } = await api.get<CursorPage<Competition>>('/competitions', {
    params: { limit: 24, ...params },
  });
  return data;
}

export async function getCompetition(id: string): Promise<Competition> {
  const { data } = await api.get<Competition>(`/competitions/${id}`);
  return data;
}

export async function listKaoyanSchools(limit = 30): Promise<CursorPage<KaoyanSchool>> {
  const { data } = await api.get<CursorPage<KaoyanSchool>>('/kaoyan/schools', {
    params: { limit },
  });
  return data;
}

export async function listKaoyanMajors(schoolId: string): Promise<CursorPage<KaoyanMajor>> {
  const { data } = await api.get<CursorPage<KaoyanMajor>>(
    `/kaoyan/schools/${schoolId}/majors`,
    { params: { limit: 50 } },
  );
  return data;
}

export async function listKaoyanScoreLines(params: {
  school_id?: string;
  major_id?: string;
  year?: number;
}): Promise<CursorPage<KaoyanScoreLine>> {
  const { data } = await api.get<CursorPage<KaoyanScoreLine>>('/kaoyan/score-lines', {
    params: { limit: 50, ...params },
  });
  return data;
}

export async function listColleges(): Promise<CollegeBrief[]> {
  const { data } = await api.get<CollegeBrief[]>('/colleges');
  return data;
}

export async function listMajors(): Promise<MajorBrief[]> {
  const { data } = await api.get<MajorBrief[]>('/majors');
  return data;
}
