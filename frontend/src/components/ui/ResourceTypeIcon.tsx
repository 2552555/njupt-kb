'use client';

import {
  BookOpen,
  Code,
  Compass,
  File,
  FileText,
  FlaskConical,
  PenTool,
  Presentation,
} from 'lucide-react';

import type { ResourceType } from '@/lib/api/types';

type IconComponent = React.ComponentType<{ className?: string }>;

// 与后端 app.core.enums.ResourceType 取值对齐（note/review/exam/lab_guide/code/slides/guide/other）。
const typeMap: Record<string, { icon: IconComponent; color: string }> = {
  note: { icon: PenTool, color: 'bg-emerald' },
  review: { icon: BookOpen, color: 'bg-violet' },
  exam: { icon: FileText, color: 'bg-cyan' },
  lab_guide: { icon: FlaskConical, color: 'bg-orange' },
  code: { icon: Code, color: 'bg-steel' },
  slides: { icon: Presentation, color: 'bg-amber' },
  guide: { icon: Compass, color: 'bg-brand' },
  other: { icon: File, color: 'bg-steel' },
};

const iconByName: Record<string, IconComponent> = {
  FileText,
  Presentation,
  PenTool,
  BookOpen,
  FlaskConical,
  Code,
  Compass,
  File,
};

export function ResourceTypeIcon({
  type,
  iconName,
  size = 'md',
}: {
  type: ResourceType | string;
  iconName?: string;
  size?: 'sm' | 'md' | 'lg';
}) {
  const entry = typeMap[type] ?? typeMap.other;
  const IconComponent = iconByName[iconName ?? ''] ?? entry.icon;
  const sizeClass =
    size === 'sm' ? 'h-8 w-8' : size === 'lg' ? 'h-12 w-12' : 'h-10 w-10';
  const iconClass = size === 'sm' ? 'h-4 w-4' : size === 'lg' ? 'h-6 w-6' : 'h-5 w-5';

  return (
    <div
      aria-hidden="true"
      className={`${entry.color} ${sizeClass} flex shrink-0 items-center justify-center text-on-color`}
    >
      <IconComponent className={iconClass} />
    </div>
  );
}
