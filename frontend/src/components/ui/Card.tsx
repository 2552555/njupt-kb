'use client';

import { ReactNode } from 'react';

interface CardProps {
  children: ReactNode;
  className?: string;
  hover?: boolean;
  onClick?: () => void;
  padding?: 'none' | 'sm' | 'md' | 'lg';
}

const paddingClasses = {
  none: '',
  sm: 'p-3',
  md: 'p-5',
  lg: 'p-6',
};

export function Card({
  children,
  className = '',
  hover = false,
  onClick,
  padding = 'md',
}: CardProps) {
  const base = `bg-surface border border-divider ${
    hover ? 'transition-shadow duration-200 hover:shadow-2 hover:-translate-y-0.5' : ''
  } ${paddingClasses[padding]} ${className}`;

  // 可点击卡片渲染为 button，保证键盘可达与可见焦点。
  if (onClick) {
    return (
      <button type="button" onClick={onClick} className={`block w-full text-left ${base}`}>
        {children}
      </button>
    );
  }

  return <div className={base}>{children}</div>;
}
