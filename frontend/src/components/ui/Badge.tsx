'use client';

import { ReactNode } from 'react';

interface BadgeProps {
  children: ReactNode;
  color?: 'indigo' | 'green' | 'red' | 'yellow' | 'blue' | 'purple' | 'gray' | 'orange' | 'teal' | 'pink';
  className?: string;
}

const colorMap: Record<string, string> = {
  indigo: 'bg-indigo-50 text-indigo-700 ring-indigo-600/20',
  green: 'bg-green-50 text-green-700 ring-green-600/20',
  red: 'bg-red-50 text-red-700 ring-red-600/20',
  yellow: 'bg-yellow-50 text-yellow-700 ring-yellow-600/20',
  blue: 'bg-blue-50 text-blue-700 ring-blue-600/20',
  purple: 'bg-purple-50 text-purple-700 ring-purple-600/20',
  gray: 'bg-gray-50 text-gray-700 ring-gray-600/20',
  orange: 'bg-orange-50 text-orange-700 ring-orange-600/20',
  teal: 'bg-teal-50 text-teal-700 ring-teal-600/20',
  pink: 'bg-pink-50 text-pink-700 ring-pink-600/20',
};

export function Badge({ children, color = 'indigo', className = '' }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${colorMap[color]} ${className}`}
    >
      {children}
    </span>
  );
}
