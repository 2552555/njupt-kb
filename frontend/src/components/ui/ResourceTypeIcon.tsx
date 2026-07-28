'use client';

import {
  FileText,
  Presentation,
  PenTool,
  BookOpen,
  FlaskConical,
  Book,
  Code,
  File,
  FileTextIcon,
} from 'lucide-react';

const iconMap: Record<string, React.ComponentType<{ className?: string }>> = {
  FileText: FileTextIcon,
  Presentation,
  PenTool,
  BookOpen,
  FlaskConical,
  Book,
  Code,
  File,
};

const colorMap: Record<string, string> = {
  exam: 'bg-blue-50 text-blue-600',
  ppt: 'bg-orange-50 text-orange-600',
  note: 'bg-green-50 text-green-600',
  review: 'bg-purple-50 text-purple-600',
  lab_report: 'bg-rose-50 text-rose-600',
  textbook: 'bg-amber-50 text-amber-600',
  code: 'bg-cyan-50 text-cyan-600',
  other: 'bg-gray-50 text-gray-600',
};

export function ResourceTypeIcon({
  type,
  iconName,
  size = 'md',
}: {
  type: string;
  iconName?: string;
  size?: 'sm' | 'md' | 'lg';
}) {
  const colorClass = colorMap[type] || colorMap.other;
  const IconComponent = iconMap[iconName || 'File'] || File;
  const sizeClass = size === 'sm' ? 'w-8 h-8 text-base' : size === 'lg' ? 'w-12 h-12 text-2xl' : 'w-10 h-10 text-xl';

  return (
    <div
      className={`${colorClass} ${sizeClass} rounded-lg flex items-center justify-center flex-shrink-0`}
    >
      <IconComponent className={size === 'sm' ? 'w-4 h-4' : size === 'lg' ? 'w-6 h-6' : 'w-5 h-5'} />
    </div>
  );
}
