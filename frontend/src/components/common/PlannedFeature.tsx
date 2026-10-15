import Link from 'next/link';

/**
 * 待评估专题的静态占位页（设计文档 §26.1、§33）。
 * 这些专题不进入首期主流程，也不从主导航进入；
 * 页面不展示任何编造的示例数据，只说明当前状态与替代入口。
 */
export function PlannedFeature({
  title,
  summary,
}: {
  title: string;
  summary: string;
}) {
  return (
    <div className="space-y-6">
      <header className="bg-steel px-6 py-8 text-on-color">
        <p className="dn-kicker">待评估 · NOT IN FIRST RELEASE</p>
        <h1 className="dn-display mt-2 text-3xl">{title}</h1>
        <p className="mt-3 max-w-2xl text-on-color">{summary}</p>
      </header>

      <div className="grid gap-4 md:grid-cols-2">
        <section className="border border-divider bg-surface p-6">
          <h2 className="text-lg font-semibold text-text-primary">当前状态</h2>
          <ul className="mt-3 space-y-2 text-sm text-text-secondary">
            <li>· 该专题不参与首期课程资料主流程，已从主导航移除。</li>
            <li>· 对应后端能力位于 <code className="text-text-primary">/api/v1/topics/*</code>，标注为待评估。</li>
            <li>· 页面不展示未经验证的示例数据、政策结论或统计数字。</li>
          </ul>
        </section>
        <section className="border border-divider bg-surface p-6">
          <h2 className="text-lg font-semibold text-text-primary">现在可以做什么</h2>
          <p className="mt-3 text-sm text-text-secondary">
            先使用课程与资料主流程查找内容。
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link
              href="/courses"
              className="inline-flex min-h-11 items-center bg-brand px-4 text-sm font-medium text-on-color hover:bg-brand/90"
            >
              浏览课程
            </Link>
            <Link
              href="/search"
              className="inline-flex min-h-11 items-center border border-control-border px-4 text-sm font-medium text-text-primary hover:bg-canvas"
            >
              搜索资料
            </Link>
          </div>
        </section>
      </div>
    </div>
  );
}
