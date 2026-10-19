/**
 * 应用段路由模板：每次切换路由时进入一次入场动效（App Router 的 template 在导航时重新挂载）。
 * 数据刷新不会走到这里，因此不会重播整页动画（DESIGN.md §13）。
 */
export default function AppTemplate({ children }: { children: React.ReactNode }) {
  return <div className="dn-enter">{children}</div>;
}
