/**
 * 资源、版本、文件与上传会话接口（backend/app/modules/content/router.py）。
 * 文件内容只走上传会话，不向接收资源 JSON 的接口混发 multipart（§16）。
 */
import { api } from './client';
import type {
  CursorPage,
  FilePurpose,
  FreshnessStatus,
  PublicationStatus,
  ResourceType,
  ReviewStatus,
  RightsBasis,
  ScanStatus,
  UploadSessionStatus,
} from './types';

// --- 请求体 ---

export interface VersionFileIn {
  file_id: string;
  purpose?: FilePurpose;
  display_order?: number;
}

export interface RightsDeclarationIn {
  rights_basis: RightsBasis;
  source?: string | null;
  original_author?: string | null;
  license?: string | null;
  /** 声明条款版本，必填。 */
  declaration_text_version: string;
}

export interface VersionFieldsIn {
  title: string;
  description?: string | null;
  change_summary?: string | null;
  preview_disabled?: boolean;
  files?: VersionFileIn[];
  rights?: RightsDeclarationIn | null;
  tags?: string[];
}

export interface ResourceCreate extends VersionFieldsIn {
  course_id: string;
  offering_id?: string | null;
  resource_type: ResourceType;
}

export type VersionCreate = VersionFieldsIn;

/** PATCH 草稿：revision 用于条件更新，过期提交返回 409。 */
export interface VersionUpdate {
  revision: number;
  title?: string | null;
  description?: string | null;
  change_summary?: string | null;
  preview_disabled?: boolean | null;
  files?: VersionFileIn[] | null;
  rights?: RightsDeclarationIn | null;
  tags?: string[] | null;
}

export interface WithdrawIn {
  reason: string;
  revision?: number | null;
}

export interface UploadCreateIn {
  original_name: string;
  declared_mime: string;
  expected_size: number;
  resource_id?: string | null;
}

// --- 响应体 ---

export interface VersionOut {
  id: string;
  resource_id: string;
  version_no: number;
  title: string;
  description: string | null;
  change_summary: string | null;
  review_status: ReviewStatus;
  preview_disabled: boolean;
  submitted_at: string | null;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface FileOut {
  id: string;
  original_name: string;
  detected_mime: string | null;
  size: number;
  sha256: string | null;
  page_count: number | null;
  scan_status: ScanStatus;
  purpose: FilePurpose;
  display_order: number;
  created_at: string;
}

export interface VersionFilesOut {
  resource_id: string;
  version_id: string;
  items: FileOut[];
}

export interface ResourceCreatedOut {
  resource_id: string;
  version_id: string;
  version_no: number;
  publication_status: PublicationStatus;
  review_status: ReviewStatus;
  revision: number;
}

export interface ResourceListItem {
  id: string;
  course_id: string;
  offering_id: string | null;
  resource_type: ResourceType;
  freshness_status: FreshnessStatus;
  title: string | null;
  version_no: number | null;
  updated_at: string;
}

export type ResourceListOut = CursorPage<ResourceListItem>;

export interface ResourceDetailOut {
  id: string;
  course_id: string;
  offering_id: string | null;
  resource_type: ResourceType;
  publication_status: PublicationStatus;
  freshness_status: FreshnessStatus;
  published_version_id: string | null;
  revision: number;
  created_at: string;
  updated_at: string;
  published_version: VersionOut | null;
  preview_available: boolean;
}

export interface PublicationOut {
  resource_id: string;
  publication_status: PublicationStatus;
  published_version_id: string | null;
  revision: number;
}

export interface SubmitOut {
  version_id: string;
  review_id: string;
  review_status: ReviewStatus;
  submitted_at: string | null;
}

export interface UploadCreateOut {
  id: string;
  status: UploadSessionStatus;
  original_name: string;
  upload_url: string;
  content_type: string;
  expires_at: string;
}

export interface UploadStatusOut {
  id: string;
  status: UploadSessionStatus;
  original_name: string;
  declared_mime: string;
  expected_size: number;
  actual_size: number | null;
  file_id: string | null;
  scan_status: ScanStatus | null;
  created_at: string;
  completed_at: string | null;
}

export interface ResourceListParams {
  course_id?: string;
  college_id?: string;
  offering_id?: string;
  semester_id?: string;
  year?: number;
  resource_type?: ResourceType;
  keyword?: string;
  sort?: 'latest' | 'popular';
  limit?: number;
  cursor?: string | null;
}

// --- 接口 ---

function idempotentConfig(idempotencyKey?: string) {
  return idempotencyKey ? { headers: { 'Idempotency-Key': idempotencyKey } } : undefined;
}

export async function listResources(
  params: ResourceListParams = {},
): Promise<ResourceListOut> {
  const { data } = await api.get<ResourceListOut>('/resources', { params });
  return data;
}

export async function getResource(resourceId: string): Promise<ResourceDetailOut> {
  const { data } = await api.get<ResourceDetailOut>(`/resources/${resourceId}`);
  return data;
}

export async function createResource(
  payload: ResourceCreate,
  idempotencyKey?: string,
): Promise<ResourceCreatedOut> {
  const { data } = await api.post<ResourceCreatedOut>(
    '/resources',
    payload,
    idempotentConfig(idempotencyKey),
  );
  return data;
}

export async function createVersion(
  resourceId: string,
  payload: VersionCreate,
  idempotencyKey?: string,
): Promise<VersionOut> {
  const { data } = await api.post<VersionOut>(
    `/resources/${resourceId}/versions`,
    payload,
    idempotentConfig(idempotencyKey),
  );
  return data;
}

export async function updateVersion(
  resourceId: string,
  versionId: string,
  payload: VersionUpdate,
): Promise<VersionOut> {
  const { data } = await api.patch<VersionOut>(
    `/resources/${resourceId}/versions/${versionId}`,
    payload,
  );
  return data;
}

export async function submitVersion(
  resourceId: string,
  versionId: string,
  idempotencyKey?: string,
): Promise<SubmitOut> {
  const { data } = await api.post<SubmitOut>(
    `/resources/${resourceId}/versions/${versionId}/submit`,
    undefined,
    idempotentConfig(idempotencyKey),
  );
  return data;
}

export async function withdrawResource(
  resourceId: string,
  payload: WithdrawIn,
  idempotencyKey?: string,
): Promise<PublicationOut> {
  const { data } = await api.post<PublicationOut>(
    `/resources/${resourceId}/withdraw`,
    payload,
    idempotentConfig(idempotencyKey),
  );
  return data;
}

export async function listVersionFiles(
  resourceId: string,
  versionId: string,
): Promise<VersionFilesOut> {
  const { data } = await api.get<VersionFilesOut>(
    `/resources/${resourceId}/versions/${versionId}/files`,
  );
  return data;
}

export async function createUpload(
  payload: UploadCreateIn,
  idempotencyKey?: string,
): Promise<UploadCreateOut> {
  const { data } = await api.post<UploadCreateOut>(
    '/uploads',
    payload,
    idempotentConfig(idempotencyKey),
  );
  return data;
}

export async function completeUpload(
  uploadId: string,
  idempotencyKey?: string,
): Promise<UploadStatusOut> {
  const { data } = await api.post<UploadStatusOut>(
    `/uploads/${uploadId}/complete`,
    undefined,
    idempotentConfig(idempotencyKey),
  );
  return data;
}

export async function getUpload(uploadId: string): Promise<UploadStatusOut> {
  const { data } = await api.get<UploadStatusOut>(`/uploads/${uploadId}`);
  return data;
}

/**
 * 直传文件到上传会话分配的对象存储 URL。
 * 使用 XHR 以获取上传进度；不经过应用 axios 实例（目标为存储入口，不是 /api/v1）。
 */
export function putFileToStorage(
  uploadUrl: string,
  file: File,
  contentType: string,
  onProgress?: (percent: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', uploadUrl, true);
    xhr.setRequestHeader('Content-Type', contentType);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else reject(new Error(`文件传输失败（HTTP ${xhr.status}）`));
    };
    xhr.onerror = () => reject(new Error('文件传输失败，请检查网络后重试'));
    xhr.send(file);
  });
}
