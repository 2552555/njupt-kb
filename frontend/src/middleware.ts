import { NextResponse, type NextRequest } from 'next/server';

/**
 * 严格脚本 CSP 与 Trusted Types（补充规范 S08）。
 *
 * 两种模式，默认先观察：
 * - `CSP_MODE=report-only`：下发 Content-Security-Policy-Report-Only，只报告不拦截；
 *   报告发往同源 `/api/v1/csp/reports`，后端只记录指令与来源，不保存完整 URL。
 * - `CSP_MODE=enforce`：下发强制策略并生成每响应 nonce；此时带 nonce 的 HTML
 *   必须是私有的，不能给其他用户复用，故对文档响应加 `Cache-Control: private, no-store`。
 * - `CSP_MODE=off`：不下发脚本策略（基础 object/base/frame/form 限制仍由 next.config 提供）。
 *
 * 约束：
 * - 不使用 `unsafe-eval`，也不为混淆放宽策略；
 * - `style-src` 保留 `unsafe-inline`：Next.js 与 Tailwind 会输出内联样式，去掉会破坏渲染；
 * - Trusted Types 默认关闭（`CSP_TRUSTED_TYPES=off`），Next/React 的兼容性需在目标浏览器
 *   实测后再强制；强制时使用受限 policy 名 `njupt-dom`，由 `src/lib/trusted-types.ts` 注册。
 */

const CSP_MODE = (process.env.CSP_MODE || 'report-only').toLowerCase();
const TRUSTED_TYPES = (process.env.CSP_TRUSTED_TYPES || 'off').toLowerCase();
const IS_PROD = process.env.NODE_ENV === 'production';
const REPORT_PATH = '/api/v1/csp/reports';

function makeNonce(): string {
  // 每响应一个 nonce；不使用可预测的计数器或时间戳。middleware 运行在 Edge Runtime，
  // 用 Web 标准的 btoa 而不是 Node Buffer。
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes));
}

function scriptPolicy(nonce: string): string {
  const directives = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,
    // Next/Tailwind 的内联样式；去掉会破坏首屏样式。
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "media-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "frame-src 'none'",
    "worker-src 'self' blob:",
    "manifest-src 'self'",
    // 同源 API 与 SSE；开发模式额外允许 HMR 的 ws。
    IS_PROD ? "connect-src 'self'" : "connect-src 'self' ws: wss:",
    `report-uri ${REPORT_PATH}`,
    'report-to csp-endpoint',
  ];
  if (IS_PROD) directives.push('upgrade-insecure-requests');
  return directives.join('; ');
}

function trustedTypesPolicy(): string {
  // policy 名受限，禁止 default：只允许站点审查过的 policy 创建可信类型。
  return "require-trusted-types-for 'script'; trusted-types njupt-dom";
}

export function middleware(request: NextRequest) {
  if (CSP_MODE === 'off') return NextResponse.next();

  const nonce = makeNonce();
  const enforce = CSP_MODE === 'enforce';

  const requestHeaders = new Headers(request.headers);
  if (enforce) requestHeaders.set('x-nonce', nonce);

  const response = NextResponse.next({ request: { headers: requestHeaders } });

  const policy = scriptPolicy(nonce);
  response.headers.set(
    enforce ? 'Content-Security-Policy' : 'Content-Security-Policy-Report-Only',
    policy,
  );

  if (TRUSTED_TYPES === 'enforce') {
    // 独立头：Trusted Types 单独强制，不与脚本策略的观察模式混在一起。
    // 多个 CSP 头按交集执行，不会覆盖上面的脚本策略。
    response.headers.append('Content-Security-Policy', trustedTypesPolicy());
  } else if (TRUSTED_TYPES === 'report') {
    response.headers.append(
      'Content-Security-Policy-Report-Only',
      `${trustedTypesPolicy()}; report-uri ${REPORT_PATH}`,
    );
  }

  // 报告分组：浏览器按 group 发送 Reporting API 报告。
  response.headers.set(
    'Reporting-Endpoints',
    `csp-endpoint="${REPORT_PATH}"`,
  );

  if (enforce) {
    // nonce 每响应不同：文档不能进共享缓存，避免把别人的 nonce 发给当前用户。
    response.headers.set('Cache-Control', 'private, no-store');
  }
  return response;
}

export const config = {
  // 只作用于文档与 RSC 请求；静态资源与图片由 Next 直接提供，不需要 nonce。
  matcher: [
    '/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|txt|xml|json|woff|woff2|ttf|map)$).*)',
  ],
};
