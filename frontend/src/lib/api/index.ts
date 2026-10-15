/** API 客户端统一出口。按模块拆分，便于定位契约来源。 */
export * from './types';
export {
  ApiError,
  API_BASE_URL,
  CSRF_COOKIE_NAME,
  CSRF_HEADER_NAME,
  describeError,
  ensureCsrfToken,
  getCsrfToken,
  setUnauthorizedHandler,
  api,
} from './client';
export * as authApi from './auth';
export * as catalogApi from './catalog';
export * as contentApi from './content';
export * as accessApi from './access';
export * as interactionApi from './interaction';
export * as notificationApi from './notification';
export * as moderationApi from './moderation';
export * as adminApi from './admin';
