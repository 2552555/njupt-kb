import type { Metadata } from 'next';

import { AuthGuard } from '@/components/layout/AuthGuard';
import { UploadFlow } from '@/components/upload/UploadFlow';

export const metadata: Metadata = {
  title: '上传与修订',
  robots: { index: false, follow: false },
};

export default function UploadPage() {
  return (
    <AuthGuard>
      <div className="space-y-6">
        <header className="bg-ink px-6 py-8 text-on-ink">
          <p className="dn-kicker">UPLOAD</p>
          <h1 className="dn-display mt-2 text-3xl">上传与修订</h1>
          <p className="mt-2 max-w-2xl text-on-ink">
            文件通过上传会话进入私有隔离区，核验并通过安全检查后才可用于发布；
            保存草稿与提交审核是两个不同动作。
          </p>
        </header>
        <UploadFlow />
      </div>
    </AuthGuard>
  );
}
