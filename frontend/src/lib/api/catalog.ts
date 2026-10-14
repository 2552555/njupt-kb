/**
 * 课程目录接口（backend/app/modules/catalog/router.py）。
 * 公开只读；不返回草稿或私有文件信息。
 */
import { api } from './client';
import type { CursorPage } from './types';

export interface CollegeBrief {
  id: string;
  name: string;
  short_name: string | null;
  code: string | null;
}

export interface MajorBrief {
  id: string;
  name: string;
  code: string | null;
  category: string | null;
  college_id: string;
  college_name: string;
}

export interface CollegeDetail extends CollegeBrief {
  majors: MajorBrief[];
}

export interface CourseListItem {
  id: string;
  code: string | null;
  name: string;
  college_id: string | null;
  college_name: string | null;
  aliases: string[];
  curriculum_count: number;
}

/** 跨模块使用的课程简要信息（INTERFACES.md catalog.course_brief）。 */
export interface CourseBrief {
  course_id: string;
  code: string | null;
  name: string;
  college_name: string | null;
  aliases: string[];
}

/** 课程在某个培养方案中的要求（课程详情「适用目录」）。 */
export interface CurriculumLink {
  curriculum_id: string;
  curriculum_name: string;
  entry_year: number | null;
  major_id: string;
  major_name: string;
  college_id: string;
  college_name: string;
  requirement_type: string;
  recommended_term: string | null;
}

export interface CourseDetail {
  id: string;
  code: string | null;
  name: string;
  description: string | null;
  status: string;
  college_id: string | null;
  college_name: string | null;
  aliases: string[];
  curricula: CurriculumLink[];
}

export interface TeacherBrief {
  teacher_id: string;
  display_name: string;
  role: string;
}

export interface OfferingItem {
  id: string;
  course_id: string;
  academic_year: string;
  season: string;
  section_code: string | null;
  assessment: string | null;
  textbook: string | null;
  syllabus: string | null;
  teachers: TeacherBrief[];
}

export interface CourseListParams {
  q?: string;
  college_id?: string;
  major_id?: string;
  curriculum_id?: string;
  semester_id?: string;
  teacher_id?: string;
  cursor?: string | null;
  limit?: number;
}

export async function listCourses(
  params: CourseListParams = {},
): Promise<CursorPage<CourseListItem>> {
  const { data } = await api.get<CursorPage<CourseListItem>>('/courses', { params });
  return data;
}

export async function getCourse(courseId: string): Promise<CourseDetail> {
  const { data } = await api.get<CourseDetail>(`/courses/${courseId}`);
  return data;
}

export async function listCourseOfferings(courseId: string): Promise<OfferingItem[]> {
  const { data } = await api.get<OfferingItem[]>(`/courses/${courseId}/offerings`);
  return data;
}

export async function listColleges(): Promise<CollegeBrief[]> {
  const { data } = await api.get<CollegeBrief[]>('/colleges');
  return data;
}

export async function getCollege(collegeId: string): Promise<CollegeDetail> {
  const { data } = await api.get<CollegeDetail>(`/colleges/${collegeId}`);
  return data;
}

export async function listMajors(collegeId?: string): Promise<MajorBrief[]> {
  const { data } = await api.get<MajorBrief[]>('/majors', {
    params: { college_id: collegeId || undefined },
  });
  return data;
}
