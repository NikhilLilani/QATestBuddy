import type { HTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

export function Alert({
  variant = 'info',
  className,
  ...props
}: HTMLAttributes<HTMLDivElement> & { variant?: 'info' | 'error' | 'success' }) {
  const variants = {
    info: 'border-brand/40 bg-brand/5 text-brand-700',
    error: 'border-red-300 bg-red-50 text-red-700',
    success: 'border-emerald-300 bg-emerald-50 text-emerald-700',
  };
  return (
    <div role="alert" className={cn('rounded-md border p-3 text-sm', variants[variant], className)} {...props} />
  );
}
