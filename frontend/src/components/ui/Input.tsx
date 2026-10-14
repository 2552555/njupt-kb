'use client';

import { InputHTMLAttributes, forwardRef, useId } from 'react';
import { AlertCircle } from 'lucide-react';

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
}

const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ label, error, className = '', id, ...props }, ref) => {
    const generatedId = useId();
    const inputId = id ?? generatedId;
    const errorId = `${inputId}-error`;

    return (
      <div className="w-full">
        {label && (
          <label htmlFor={inputId} className="mb-1 block text-sm font-medium text-text-primary">
            {label}
          </label>
        )}
        <input
          ref={ref}
          id={inputId}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          className={`block min-h-11 w-full border bg-surface px-3 py-2 text-sm text-text-primary transition-colors placeholder:text-text-secondary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus disabled:cursor-not-allowed disabled:bg-canvas ${
            error ? 'border-crimson' : 'border-control-border'
          } ${className}`}
          {...props}
        />
        {error && (
          <p id={errorId} role="alert" className="mt-1 flex items-center gap-1 text-xs text-text-primary">
            <AlertCircle className="h-3.5 w-3.5 shrink-0 text-crimson" aria-hidden="true" />
            {error}
          </p>
        )}
      </div>
    );
  },
);

Input.displayName = 'Input';

export { Input };
export type { InputProps };
