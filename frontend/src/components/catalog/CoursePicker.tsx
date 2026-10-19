'use client';

import { useId, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { listCourses } from '@/lib/api/catalog';
import { describeError } from '@/lib/api/client';

export function CoursePicker({ value, label, onChange, disabled = false }: {
  value: string; label?: string; onChange: (id: string) => void; disabled?: boolean;
}) {
  const id = useId();
  const [query, setQuery] = useState('');
  const result = useQuery({ queryKey: ['course-picker', query], queryFn: () => listCourses({ q: query || undefined, limit: 20 }) });
  return <fieldset disabled={disabled} className="min-w-0 space-y-2">
    <label htmlFor={`${id}-search`} className="block text-sm font-medium">{label || '选择课程'}</label>
    <input id={`${id}-search`} placeholder="搜索课程名称或代码" value={query} onChange={e => setQuery(e.target.value)} className="min-h-11 w-full border border-control-border bg-surface px-3 text-sm" />
    <select aria-label={label || '选择课程'} value={value} onChange={e => onChange(e.target.value)} className="min-h-11 w-full border border-control-border bg-surface px-3 text-sm">
      <option value="">请选择课程</option>
      {value && !result.data?.items.some(c => c.id === value) && <option value={value}>已选课程 · {value}</option>}
      {result.data?.items.map(c => <option key={c.id} value={c.id}>{c.name} · {c.code || '无代码'}{c.college_name ? ` · ${c.college_name}` : ''}</option>)}
    </select>
    {result.error && <p role="alert" className="text-sm text-crimson">{describeError(result.error).message}</p>}
    {result.data?.has_more && <p className="text-xs text-text-secondary">仅展示前 20 项，请输入更完整的课程名称或代码。</p>}
  </fieldset>;
}
