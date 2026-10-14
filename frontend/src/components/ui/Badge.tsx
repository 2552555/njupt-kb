'use client';

import { ReactNode } from 'react';

interface BadgeProps {
  children: ReactNode;
  color?: 'indigo' | 'green' | 'red' | 'yellow' | 'blue' | 'purple' | 'gray' | 'orange' | 'teal' | 'pink';
  className?: string;
}

// DNDL：默认直角、使用品牌实色色块；正文用 --dn-text-on-color 保证 8 色上的可读性。
// 状态不能只靠颜色：Badge 内始终携带文字。
const colorMap: Record<string, string> = {
  indigo: 'bg-brand text-on-color',
  teal: 'bg-brand text-on-color',
  green: 'bg-emerald text-on-color',
  red: 'bg-crimson text-on-color',
  pink: 'bg-crimson text-on-color',
  yellow: 'bg-amber text-on-color',
  blue: 'bg-cyan text-on-color',
  purple: 'bg-violet text-on-color',
  gray: 'bg-steel text-on-color',
  orange: 'bg-orange text-on-color',
};

export function Badge({ children, color = 'gray', className = '' }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 text-xs font-semibold ${colorMap[color] ?? colorMap.gray} ${className}`}
    >
      {children}
    </span>
  );
}
