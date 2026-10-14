'use client';

import { useRouter } from 'next/navigation';
import { FormEvent, useState } from 'react';
import { Search } from 'lucide-react';

/**
 * 课程/资料搜索框。提交后跳转到 /search，由搜索页统一处理筛选与分页。
 * 表单带可见标签（视觉隐藏但可读屏），移动端可完成搜索（§39 A20）。
 */
export function SearchBox({
  defaultValue = '',
  size = 'md',
  placeholder = '输入课程名、课程代码、简称或资料标题',
}: {
  defaultValue?: string;
  size?: 'md' | 'lg';
  placeholder?: string;
}) {
  const router = useRouter();
  const [value, setValue] = useState(defaultValue);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const keyword = value.trim();
    router.push(keyword ? `/search?q=${encodeURIComponent(keyword)}` : '/search');
  }

  const inputClass =
    size === 'lg'
      ? 'min-h-13 px-4 text-base'
      : 'min-h-11 px-3 text-sm';

  return (
    <form onSubmit={handleSubmit} role="search" className="flex w-full">
      <label htmlFor="site-search" className="sr-only">
        搜索课程或资料
      </label>
      <input
        id="site-search"
        type="search"
        name="q"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder={placeholder}
        className={`w-full border border-control-border bg-surface text-text-primary placeholder:text-text-secondary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus ${inputClass}`}
      />
      <button
        type="submit"
        className={`flex shrink-0 items-center gap-2 bg-ink px-5 font-medium text-on-ink hover:bg-ink/90 ${
          size === 'lg' ? 'min-h-13 text-base' : 'min-h-11 text-sm'
        }`}
      >
        <Search className="h-4 w-4" aria-hidden="true" />
        搜索
      </button>
    </form>
  );
}
