import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

/**
 * 攻略正文渲染（Markdown）。
 *
 * 安全边界：
 * - 不启用 `rehype-raw`，原始 HTML 不进入 DOM；作者输入始终按文本处理。
 * - 链接统一加 `rel="noopener noreferrer nofollow"` 并在新窗口打开；
 * - 图片只允许 http/https 与站内相对路径，拒绝 data:/javascript:。
 * - 样式沿用 DNDL：直角、语义色变量，不引入圆角卡片。
 */
export function MarkdownBody({ markdown }: { markdown: string }) {
  return (
    <div className="space-y-4 text-base leading-7 text-text-primary">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => <h2 className="dn-display mt-6 text-2xl">{children}</h2>,
          h2: ({ children }) => <h3 className="mt-6 text-xl font-semibold">{children}</h3>,
          h3: ({ children }) => <h4 className="mt-4 text-lg font-medium">{children}</h4>,
          p: ({ children }) => <p className="whitespace-pre-wrap">{children}</p>,
          ul: ({ children }) => <ul className="list-disc space-y-1 pl-6">{children}</ul>,
          ol: ({ children }) => <ol className="list-decimal space-y-1 pl-6">{children}</ol>,
          blockquote: ({ children }) => (
            <blockquote className="border-l-4 border-brand bg-surface px-4 py-2 text-text-secondary">
              {children}
            </blockquote>
          ),
          code: ({ children }) => (
            <code className="bg-canvas px-1 py-0.5 font-mono text-sm">{children}</code>
          ),
          pre: ({ children }) => (
            <pre className="overflow-x-auto border border-divider bg-canvas p-4 font-mono text-sm">
              {children}
            </pre>
          ),
          a: ({ href, children }) => {
            const safe = typeof href === 'string' && /^(https?:\/\/|\/)/.test(href);
            if (!safe) return <span>{children}</span>;
            return (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer nofollow"
                className="text-brand underline underline-offset-2"
              >
                {children}
              </a>
            );
          },
          img: ({ src, alt }) => {
            const safe = typeof src === 'string' && /^(https?:\/\/|\/)/.test(src);
            if (!safe) return null;
            return (
              // 外链图片可能失效，保留 alt 文案，不阻塞正文阅读。
              // eslint-disable-next-line @next/next/no-img-element
              <img src={src} alt={alt ?? ''} className="max-w-full border border-divider" />
            );
          },
          table: ({ children }) => (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-sm">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border border-divider bg-canvas px-3 py-2 text-left">{children}</th>
          ),
          td: ({ children }) => <td className="border border-divider px-3 py-2">{children}</td>,
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
