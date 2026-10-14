/**
 * 与后端 DTO 对应的共享类型与受约束枚举。
 *
 * 契约来源：docs/INTERFACES.md、docs/ARCHITECTURE.md §4、backend/app/core/enums.py。
 * 约定：主键一律 `string`（UUID 以字符串传输，前端不得声明为 number）；
 * 时间一律 ISO 8601 字符串；文件大小用整数字节；金额用字符串定点数。
 */

/** 列表统一分页结构（ARCHITECTURE.md §4）。 */
export interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
}

/** 统一错误体（ARCHITECTURE.md §4）。 */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    request_id: string;
    details?: Record<string, unknown> | null;
  };
}

/** 空操作结果说明。 */
export interface ActionMessage {
  message: string;
  mail_sent?: boolean;
  note?: string | null;
}

// --- 枚举（与 app/core/enums.py 取值一一对应）---

export type AccountStatus =
  | 'pending_verification'
  | 'active'
  | 'suspended'
  | 'banned'
  | 'deleted';

export type Role = 'user' | 'contributor' | 'reviewer' | 'admin';

export type ResourceType =
  | 'note'
  | 'review'
  | 'exam'
  | 'lab_guide'
  | 'code'
  | 'slides'
  | 'guide'
  | 'other';

export type ReviewStatus = 'draft' | 'pending' | 'approved' | 'rejected';

export type PublicationStatus =
  | 'unpublished'
  | 'published'
  | 'withdrawn'
  | 'archived';

export type FreshnessStatus = 'current' | 'possibly_outdated' | 'outdated';

export type ScanStatus = 'pending' | 'running' | 'passed' | 'rejected' | 'failed';

export type UploadSessionStatus =
  | 'initiated'
  | 'uploading'
  | 'received'
  | 'verified'
  | 'expired'
  | 'aborted';

export type FilePurpose = 'primary' | 'attachment' | 'sample';

export type RightsBasis =
  | 'original'
  | 'authorized'
  | 'open_license'
  | 'third_party'
  | 'unverified';

export type ModerationDecisionType = 'approve' | 'reject';

export type ReportStatus =
  | 'open'
  | 'triaged'
  | 'investigating'
  | 'resolved'
  | 'dismissed'
  | 'appealed';

export type ScopeType =
  | 'public'
  | 'authenticated'
  | 'verified_school'
  | 'explicit_grant'
  | 'private';

export type Capability =
  | 'read_metadata'
  | 'preview'
  | 'read_full'
  | 'download_original';

export type EntitlementSourceType =
  | 'free_open'
  | 'author'
  | 'admin_grant'
  | 'order'
  | 'subscription';

export type NotificationType =
  | 'submission_received'
  | 'review_result'
  | 'version_published'
  | 'resource_withdrawn'
  | 'report_result'
  | 'correction_result'
  | 'request_updated';

/** 资源类型的中文标签与顺序，供筛选控件与列表统一使用。 */
export const RESOURCE_TYPE_ORDER: ResourceType[] = [
  'note',
  'review',
  'exam',
  'lab_guide',
  'code',
  'slides',
  'guide',
  'other',
];

export const RESOURCE_TYPE_LABELS: Record<ResourceType, string> = {
  note: '笔记',
  review: '复习资料',
  exam: '历年试卷',
  lab_guide: '实验指导',
  code: '代码',
  slides: '课件',
  guide: '课程攻略',
  other: '其他',
};

export function isResourceType(value: string): value is ResourceType {
  return (RESOURCE_TYPE_ORDER as string[]).includes(value);
}
