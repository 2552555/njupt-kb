/**
 * 访问授权接口（backend/app/modules/access/router.py）。
 * 三类访问：预览、完整阅读、下载原件（§17.1）。
 * 授权前由服务端检查范围、状态、风险与额度；客户端不提交「已付费/已审核」字段。
 */
import { api } from './client';
import type { Capability, CursorPage, EntitlementSourceType, ScopeType } from './types';

export interface PreviewPage {
  page_no: number;
  url: string;
  url_expires_at: string;
}

export interface PreviewGrantResult {
  grant_id: string;
  preview_url: string;
  url_expires_at: string;
  file_name: string;
  version_no: number;
  /**
   * 预览形态（prevew.md 五）：image=水印 WebP 图片；pdf=受限预览 PDF；thumbnail=缩略图。
   * 旧版接口或 fixture 可能不带该字段，缺省按 pdf 处理。
   */
  preview_type?: 'image' | 'pdf' | 'thumbnail';
  /** 多页图片预览：逐页短期 URL，最多 PREVIEW_IMAGE_MAX_PAGES 页；缺省时只用 preview_url。 */
  pages?: PreviewPage[];
  /** 图片预览是否由服务端叠加水印（匿名与登录都有）。 */
  watermarked?: boolean;
}

export interface DownloadGrantResult {
  grant_id: string;
  download_url: string;
  url_expires_at: string;
  file_name: string;
  version_no: number;
}

export interface EntitlementOut {
  id: string;
  user_id: string;
  scope_type: ScopeType;
  scope_id: string;
  capability: Capability;
  source_type: EntitlementSourceType;
  source_id: string | null;
  starts_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  revoked_reason: string | null;
  created_at: string;
}

/** 申请预览凭证（公开预览对游客开放，具体范围由服务端判定）。 */
export async function createPreviewGrant(
  resourceId: string,
  versionId: string,
): Promise<PreviewGrantResult> {
  const { data } = await api.post<PreviewGrantResult>(
    `/resources/${resourceId}/preview-grants`,
    { version_id: versionId },
  );
  return data;
}

/**
 * 申请下载授权。幂等键避免重试重复占用额度；相同键返回同一业务结果。
 * 返回的 download_url 是短期链接，浏览器据此直接取文件。
 */
export async function createDownloadGrant(
  resourceId: string,
  versionId: string,
  fileId: string,
  idempotencyKey: string,
): Promise<DownloadGrantResult> {
  const { data } = await api.post<DownloadGrantResult>(
    `/resources/${resourceId}/download-grants`,
    { version_id: versionId, file_id: fileId },
    { headers: { 'Idempotency-Key': idempotencyKey } },
  );
  return data;
}

export async function listMyEntitlements(
  cursor?: string | null,
): Promise<CursorPage<EntitlementOut>> {
  const { data } = await api.get<CursorPage<EntitlementOut>>('/me/entitlements', {
    params: { cursor: cursor || undefined },
  });
  return data;
}
