'use client';

import { LucideIcon, Inbox } from 'lucide-react';
import { Button } from './Button';

interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  description?: string;
  actionLabel?: string;
  onAction?: () => void;
}

export function EmptyState({
  icon: Icon = Inbox,
  title,
  description,
  actionLabel,
  onAction,
}: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center px-4 py-16 text-center">
      <div className="mb-4 flex h-16 w-16 items-center justify-center bg-steel text-on-color">
        <Icon className="h-8 w-8" aria-hidden="true" />
      </div>
      <h3 className="mb-1 text-lg font-semibold text-text-primary">{title}</h3>
      {description && (
        <p className="mb-4 max-w-sm text-sm text-text-secondary">{description}</p>
      )}
      {actionLabel && onAction && (
        <Button variant="primary" onClick={onAction}>
          {actionLabel}
        </Button>
      )}
    </div>
  );
}
