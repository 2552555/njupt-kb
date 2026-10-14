import Link from 'next/link';

/**
 * 404：可能因为资料已下架、尚未发布或链接有误（§26.3）。
 * 不把内容状态问题显示成网络故障，并给出可继续操作的入口。
 */
export default function NotFound() {
  return (
    <div className="mx-auto flex min-h-screen max-w-2xl flex-col items-center justify-center px-4 py-16 text-center">
      <p className="dn-kicker text-text-secondary">NOT FOUND</p>
      <h1 className="dn-display mt-2 text-4xl">找不到这个页面</h1>
      <p className="mt-4 text-text-secondary">
        可能的原因：资料已下架或尚未发布、链接已失效、或地址输入有误。
        这不一定是网络故障；私有资源对无权限的访问会统一返回「不存在」以避免泄露其存在性。
      </p>
      <div className="mt-8 flex flex-wrap justify-center gap-3">
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
      <p className="mt-8 text-sm text-text-secondary">
        如果你认为这是误判，可以在资料页使用「举报或纠错」提交反馈。
      </p>
    </div>
  );
}
