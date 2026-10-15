import { format, formatDistanceToNow } from 'date-fns';
import { zhCN } from 'date-fns/locale';

import { RESOURCE_TYPE_LABELS, type ResourceType } from '@/lib/api/types';

export function formatDate(date: string | Date): string {
  return format(new Date(date), 'yyyy年M月d日', { locale: zhCN });
}

export function formatDateTime(date: string | Date): string {
  return format(new Date(date), 'yyyy年M月d日 HH:mm', { locale: zhCN });
}

export function formatRelative(date: string | Date): string {
  return formatDistanceToNow(new Date(date), {
    addSuffix: true,
    locale: zhCN,
  });
}

export function formatFileSize(bytes: number): string {
  if (!bytes || bytes <= 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

export function formatCount(count: number): string {
  if (count >= 10000) {
    return `${(count / 10000).toFixed(1)}万`;
  }
  if (count >= 1000) {
    return `${(count / 1000).toFixed(1)}千`;
  }
  return count.toString();
}

/** 资源类型的展示信息；取值与后端 app.core.enums.ResourceType 一致。 */
export const RESOURCE_TYPES: Record<
  ResourceType,
  { label: string; icon: string; color: string }
> = {
  note: { label: RESOURCE_TYPE_LABELS.note, icon: 'PenTool', color: 'bg-emerald text-on-color' },
  review: { label: RESOURCE_TYPE_LABELS.review, icon: 'BookOpen', color: 'bg-violet text-on-color' },
  exam: { label: RESOURCE_TYPE_LABELS.exam, icon: 'FileText', color: 'bg-cyan text-on-color' },
  lab_guide: {
    label: RESOURCE_TYPE_LABELS.lab_guide,
    icon: 'FlaskConical',
    color: 'bg-orange text-on-color',
  },
  code: { label: RESOURCE_TYPE_LABELS.code, icon: 'Code', color: 'bg-steel text-on-color' },
  slides: {
    label: RESOURCE_TYPE_LABELS.slides,
    icon: 'Presentation',
    color: 'bg-amber text-on-color',
  },
  guide: { label: RESOURCE_TYPE_LABELS.guide, icon: 'Compass', color: 'bg-brand text-on-color' },
  other: { label: RESOURCE_TYPE_LABELS.other, icon: 'File', color: 'bg-steel text-on-color' },
};

export function getResourceTypeInfo(type: string) {
  return (
    RESOURCE_TYPES[type as ResourceType] ?? {
      label: RESOURCE_TYPE_LABELS.other,
      icon: 'File',
      color: 'bg-steel text-on-color',
    }
  );
}

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** 无效 UUID 会让后端返回输入错误；前端在发请求前先过滤掉明显非法的值。 */
export function isUuid(value: string | null | undefined): value is string {
  return typeof value === 'string' && UUID_PATTERN.test(value);
}
