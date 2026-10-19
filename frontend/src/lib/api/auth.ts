/**
 * 身份与会话接口（backend/app/modules/identity/router.py）。
 * 会话通过 Cookie 维持，本模块不接触任何令牌。
 */
import { api } from './client';
import type { AccountStatus, ActionMessage, CursorPage, Role } from './types';

export interface RegisterRequest {
  email: string;
  password: string;
  display_name?: string;
}

export interface RegisterResponse {
  id: string;
  email: string;
  status: AccountStatus;
  email_verification_sent: boolean;
  message: string;
  note: string | null;
}

export interface LoginRequest {
  email: string;
  password: string;
  otp?: string;
}

export interface MeResponse {
  id: string;
  email: string;
  role: Role;
  permissions?: string[];
  status: AccountStatus;
  display_name: string;
  avatar_url: string | null;
  gender?: 'male' | 'female' | 'nonbinary' | 'undisclosed' | null;
  bio?: string | null;
  email_verified: boolean;
  /** 校园身份校验方案确定前恒为 false，不宣称完成官方学生身份认证。 */
  school_verified: boolean;
  email_verified_at: string | null;
  college_id: string | null;
  major_id: string | null;
  enrollment_year: number | null;
  preferences: Record<string, unknown>;
  created_at: string;
}

export interface MeUpdateRequest {
  display_name?: string | null;
  avatar_url?: string | null;
  gender?: MeResponse['gender'];
  bio?: string | null;
  college_id?: string | null;
  major_id?: string | null;
  enrollment_year?: number | null;
  preferences?: Record<string, unknown> | null;
}

export interface SessionItem {
  id: string;
  issued_at: string;
  expires_at: string;
  last_seen_at: string | null;
  revoked_at: string | null;
  revoke_reason: string | null;
  user_agent: string | null;
  client_ip: string | null;
  current: boolean;
}

export async function register(payload: RegisterRequest): Promise<RegisterResponse> {
  const { data } = await api.post<RegisterResponse>('/auth/register', payload);
  return data;
}

export async function requestEmailVerification(email: string): Promise<ActionMessage> {
  const { data } = await api.post<ActionMessage>('/auth/email-verifications', { email });
  return data;
}

export async function confirmEmailVerification(token: string): Promise<ActionMessage> {
  const { data } = await api.post<ActionMessage>('/auth/email-verifications/confirm', {
    token,
  });
  return data;
}

export async function login(payload: LoginRequest): Promise<MeResponse> {
  const { data } = await api.post<MeResponse>('/auth/login', payload);
  return data;
}

export async function logout(): Promise<void> {
  await api.post('/auth/logout');
}

export async function requestPasswordReset(email: string): Promise<ActionMessage> {
  const { data } = await api.post<ActionMessage>('/auth/password-resets', { email });
  return data;
}

export async function confirmPasswordReset(
  token: string,
  newPassword: string,
): Promise<ActionMessage> {
  const { data } = await api.post<ActionMessage>('/auth/password-resets/confirm', {
    token,
    new_password: newPassword,
  });
  return data;
}

/** 应用启动时的会话探测：未登录返回 null，不触发跳转。 */
export async function fetchMe(): Promise<MeResponse | null> {
  try {
    const { data } = await api.get<MeResponse>('/me', { skipAuthRedirect: true });
    return data;
  } catch {
    return null;
  }
}

export async function updateMe(payload: MeUpdateRequest): Promise<MeResponse> {
  const { data } = await api.patch<MeResponse>('/me', payload);
  return data;
}

/** 头像直接上传到后端公开存储，成功返回更新后的个人资料。 */
export async function uploadAvatar(file: File): Promise<MeResponse> {
  const form = new FormData();
  form.append('file', file);
  const { data } = await api.post<MeResponse>('/me/avatar', form);
  return data;
}

export async function listSessions(cursor?: string | null): Promise<CursorPage<SessionItem>> {
  const { data } = await api.get<CursorPage<SessionItem>>('/me/sessions', {
    params: { cursor: cursor || undefined },
  });
  return data;
}

export async function revokeSession(sessionId: string): Promise<void> {
  await api.delete(`/me/sessions/${sessionId}`);
}

export interface ChangePasswordRequest {
  current_password: string;
  new_password: string;
}

/** 登录态修改密码：成功后其他设备会话被撤销，当前会话保留。 */
export async function changePassword(payload: ChangePasswordRequest): Promise<ActionMessage> {
  const { data } = await api.post<ActionMessage>('/me/password', payload);
  return data;
}

/** 一键退出其他设备：撤销除当前会话外的全部会话。 */
export async function revokeOtherSessions(): Promise<ActionMessage> {
  const { data } = await api.post<ActionMessage>('/me/sessions/revoke-others');
  return data;
}

export interface DeleteAccountRequest {
  /** 当前密码：服务端复用既有密码校验实现，不另发明哈希逻辑。 */
  password: string;
  /** 固定确认词，防止误触；不匹配时服务端拒绝。 */
  confirm: string;
}

/** 注销账号：服务端匿名化资料、撤销全部会话，业务事实保留（§9）。 */
export async function deleteAccount(payload: DeleteAccountRequest): Promise<ActionMessage> {
  const { data } = await api.post<ActionMessage>('/me/deletion', payload);
  return data;
}

/** 导出本人数据的 JSON 附件（§9）。返回文件名与 Blob，由调用方触发保存。 */
export async function exportMyData(): Promise<{ blob: Blob; filename: string }> {
  const response = await api.get<Blob>('/me/export', { responseType: 'blob' });
  const disposition = response.headers['content-disposition'] as string | undefined;
  const match = disposition?.match(/filename="?([^";]+)"?/i);
  return {
    blob: response.data,
    filename: match?.[1] ?? 'njupt-kb-export.json',
  };
}
