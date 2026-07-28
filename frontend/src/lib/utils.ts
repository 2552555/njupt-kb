import { format, formatDistanceToNow } from 'date-fns';
import { zhCN } from 'date-fns/locale';

export function formatDate(date: string | Date): string {
  return format(new Date(date), 'yyyy年M月d日', { locale: zhCN });
}

export function formatRelative(date: string | Date): string {
  return formatDistanceToNow(new Date(date), {
    addSuffix: true,
    locale: zhCN,
  });
}

export function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

export function formatCount(count: number): string {
  if (count >= 10000) {
    return (count / 10000).toFixed(1) + '万';
  }
  if (count >= 1000) {
    return (count / 1000).toFixed(1) + '千';
  }
  return count.toString();
}

export const RESOURCE_TYPES: Record<
  string,
  { label: string; icon: string; color: string }
> = {
  exam: { label: '试卷', icon: 'FileText', color: 'text-blue-600 bg-blue-50' },
  ppt: { label: 'PPT课件', icon: 'Presentation', color: 'text-orange-600 bg-orange-50' },
  note: { label: '笔记', icon: 'PenTool', color: 'text-green-600 bg-green-50' },
  review: { label: '复习资料', icon: 'BookOpen', color: 'text-purple-600 bg-purple-50' },
  lab_report: {
    label: '实验报告',
    icon: 'FlaskConical',
    color: 'text-rose-600 bg-rose-50',
  },
  textbook: { label: '教材', icon: 'Book', color: 'text-amber-600 bg-amber-50' },
  code: { label: '代码', icon: 'Code', color: 'text-cyan-600 bg-cyan-50' },
  other: { label: '其他', icon: 'File', color: 'text-gray-600 bg-gray-50' },
};

export function getResourceTypeInfo(type: string) {
  return (
    RESOURCE_TYPES[type] || {
      label: type || '未知',
      icon: 'File',
      color: 'text-gray-600 bg-gray-50',
    }
  );
}
