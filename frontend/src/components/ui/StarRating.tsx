'use client';

import { Star } from 'lucide-react';

interface StarRatingProps {
  rating: number;
  maxStars?: number;
  size?: 'sm' | 'md' | 'lg';
  showValue?: boolean;
}

const sizeClasses = {
  sm: 'h-3.5 w-3.5',
  md: 'h-5 w-5',
  lg: 'h-6 w-6',
};

export function StarRating({
  rating,
  maxStars = 5,
  size = 'md',
  showValue = false,
}: StarRatingProps) {
  const rounded = Math.round(rating * 10) / 10;

  return (
    <div
      className="flex items-center gap-0.5"
      role="img"
      aria-label={`评分 ${rounded.toFixed(1)} 分（满分 ${maxStars} 分）`}
    >
      {Array.from({ length: maxStars }).map((_, i) => (
        <Star
          key={i}
          aria-hidden="true"
          className={`${sizeClasses[size]} ${
            i < Math.round(rating) ? 'fill-amber text-amber' : 'text-divider'
          }`}
        />
      ))}
      {showValue && (
        <span className="ml-1.5 text-sm text-text-secondary">{rounded.toFixed(1)}</span>
      )}
    </div>
  );
}
