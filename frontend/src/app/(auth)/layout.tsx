import Link from 'next/link';

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      {/* 大面积品牌实色色块，而非白色圆角卡片（DESIGN.md §5.1）。 */}
      <section className="hidden bg-brand p-10 text-on-color lg:flex lg:flex-col lg:justify-between">
        <Link href="/" className="flex items-center gap-2">
          <span className="grid h-9 w-9 place-items-center bg-on-color/15 text-base font-semibold">
            邮
          </span>
          <span className="dn-display text-lg">南邮课程知识平台</span>
        </Link>
        <div>
          <p className="dn-kicker">NJUPT COURSE KNOWLEDGE</p>
          <h1 className="dn-display mt-3 text-4xl">
            以课程为线索，
            <br />
            找到可追溯的学习资料
          </h1>
          <p className="mt-4 max-w-sm text-on-color">
            按课程、学期与资料类型查找内容，查看适用范围与预览，再按权限阅读或下载。
          </p>
        </div>
        <p className="text-sm text-on-color">
          邮箱验证只证明你当时控制该邮箱，不代表完成官方学生身份认证。
        </p>
      </section>

      <section className="flex items-center justify-center px-4 py-10">
        <div className="w-full max-w-md">{children}</div>
      </section>
    </div>
  );
}
