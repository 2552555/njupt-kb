'use client';

import { useState } from 'react';
import toast from 'react-hot-toast';
import { Download } from 'lucide-react';

import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { exportAuditCsv } from '@/lib/api/analytics';
import { ApiError, describeError } from '@/lib/api/client';

/** 日期输入使用本地日期，发送前转成 ISO（UTC），避免时区悄悄改变窗口。 */
function localDate(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
}

function toIso(value: string, endOfDay: boolean): string | undefined {
  if (!value) return undefined;
  const date = new Date(`${value}T${endOfDay ? '23:59:59.999' : '00:00:00'}`);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

/**
 * 审计 CSV 导出（beta16 §44）：内容由后端生成，前端只负责参数与下载。
 * 不传 `limit`：行数上限由服务端配置决定。
 */
export function AuditExportPanel() {
  const [from, setFrom] = useState(() => localDate(new Date(Date.now() - 6 * 86_400_000)));
  const [to, setTo] = useState(() => localDate(new Date()));
  const [action, setAction] = useState('');
  const [pending, setPending] = useState(false);

  async function handleExport() {
    if (from && to && from > to) {
      toast.error('开始日期不能晚于结束日期');
      return;
    }

    setPending(true);
    try {
      const file = await exportAuditCsv({
        from: toIso(from, false),
        to: toIso(to, true),
        action: action.trim() || undefined,
      });

      const url = URL.createObjectURL(file.blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = file.filename;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      // 延迟释放，避免部分浏览器在下载开始前就失效。
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);

      toast.success(
        file.truncated
          ? '已导出，超过服务端上限的部分已截断'
          : file.rows === null
            ? '审计记录已开始下载'
            : `已导出 ${file.rows} 行审计记录`,
      );
    } catch (error) {
      toast.error(
        error instanceof ApiError && error.status === 403
          ? '当前账号没有导出审计记录的权限'
          : describeError(error).message,
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <section
      aria-labelledby="audit-export"
      className="space-y-3 border border-divider bg-surface p-4"
    >
      <div>
        <h3 id="audit-export" className="dn-display text-lg">
          导出 CSV
        </h3>
        <p className="mt-1 text-xs text-text-secondary">
          导出审计元数据，不含变更前后 JSON。默认最近 7 天；行数上限由服务端决定，超过上限时自动截断。
        </p>
      </div>
      <div className="grid items-end gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Input
          label="开始日期"
          type="date"
          value={from}
          onChange={(event) => setFrom(event.target.value)}
        />
        <Input
          label="结束日期"
          type="date"
          value={to}
          onChange={(event) => setTo(event.target.value)}
        />
        <Input
          label="动作前缀（可选）"
          placeholder="如 review."
          maxLength={80}
          value={action}
          onChange={(event) => setAction(event.target.value)}
        />
        <Button type="button" onClick={() => void handleExport()} isLoading={pending}>
          <Download className="h-4 w-4" aria-hidden="true" />
          导出 CSV
        </Button>
      </div>
    </section>
  );
}
