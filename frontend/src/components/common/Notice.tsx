'use client';

import { ReactNode } from 'react';
import { AlertTriangle, CheckCircle2, Info, XCircle } from 'lucide-react';

type Tone = 'info' | 'warning' | 'error' | 'success';

const toneConfig: Record<
  Tone,
  { border: string; icon: string; Icon: React.ComponentType<{ className?: string }>; label: string }
> = {
  info: { border: 'border-l-cyan', icon: 'text-cyan', Icon: Info, label: '提示' },
  warning: { border: 'border-l-amber', icon: 'text-amber', Icon: AlertTriangle, label: '注意' },
  error: { border: 'border-l-crimson', icon: 'text-crimson', Icon: XCircle, label: '错误' },
  success: { border: 'border-l-emerald', icon: 'text-emerald', Icon: CheckCircle2, label: '完成' },
};

/**
 * 状态提示：颜色之外始终带图标与文字说明（DESIGN.md §12.1「状态不能只用颜色」）。
 * 文案应说明「发生了什么、接下来怎么做」（§26.2）。
 */
export function Notice({
  tone = 'info',
  title,
  children,
  action,
}: {
  tone?: Tone;
  title?: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  const config = toneConfig[tone];
  const Icon = config.Icon;

  return (
    <div className={`border border-divider border-l-4 ${config.border} bg-surface p-4`}>
      <div className="flex gap-3">
        <Icon className={`mt-0.5 h-5 w-5 shrink-0 ${config.icon}`} aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-text-primary">
            <span className="sr-only">{config.label}：</span>
            {title}
          </p>
          {children && <div className="mt-1 text-sm text-text-secondary">{children}</div>}
          {action && <div className="mt-3">{action}</div>}
        </div>
      </div>
    </div>
  );
}
