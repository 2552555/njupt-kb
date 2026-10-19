/**
 * Trusted Types 受限 policy（补充规范 S08）。
 *
 * 本站前端不使用 `dangerouslySetInnerHTML`，也不需要动态执行字符串脚本；
 * 因此 `njupt-dom` **不提供** HTML/脚本注入能力，任何字符串写入危险 sink 都会被拒绝。
 * 未来若确需渲染富文本，必须在这里加入经过审查的净化实现，不能改成原样放行。
 *
 * policy 名必须与 `src/middleware.ts` 中 `trusted-types njupt-dom` 一致。
 */

type TrustedTypesPolicy = {
  createHTML?: (input: string) => string;
  createScript?: (input: string) => string;
  createScriptURL?: (input: string) => string;
};

type TrustedTypesApi = {
  createPolicy: (name: string, rules: TrustedTypesPolicy) => unknown;
};

export function installTrustedTypesPolicy(): void {
  if (typeof window === 'undefined') return;
  const api = (window as unknown as { trustedTypes?: TrustedTypesApi }).trustedTypes;
  if (!api || typeof api.createPolicy !== 'function') return;

  try {
    api.createPolicy('njupt-dom', {
      createHTML: () => {
        throw new Error('njupt-dom: 禁止把字符串写入 HTML sink');
      },
      createScript: () => {
        throw new Error('njupt-dom: 禁止动态脚本字符串');
      },
      createScriptURL: (url: string) => {
        // 只接受同源相对路径或同源绝对地址；拒绝 javascript:/data: 与跨站脚本地址。
        const value = String(url).trim();
        const safe =
          value.startsWith('/') && !value.startsWith('//') && !value.includes('\\');
        if (safe) return value;
        try {
          const parsed = new URL(value, window.location.origin);
          if (parsed.origin === window.location.origin) return parsed.toString();
        } catch {
          /* 解析失败按不安全处理 */
        }
        throw new Error('njupt-dom: 不允许的脚本地址');
      },
    });
  } catch {
    // 同名 policy 已存在（重复挂载）或浏览器实现差异：忽略，不阻断渲染。
  }
}
