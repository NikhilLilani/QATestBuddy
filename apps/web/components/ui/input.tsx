import { forwardRef, type InputHTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  error?: string | boolean;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, error, 'aria-invalid': ariaInvalid, ...props },
  ref,
) {
  const isInvalid = Boolean(error) || ariaInvalid === true || ariaInvalid === 'true';
  return (
    <input
      ref={ref}
      aria-invalid={isInvalid || undefined}
      className={cn(
        'h-10 w-full rounded-md border bg-transparent px-3 text-sm shadow-sm',
        'transition-[border-color,box-shadow] duration-150 ease-out',
        'placeholder:text-[hsl(var(--muted-foreground))]',
        'focus:outline-none focus:ring-2 focus:ring-offset-1',
        'disabled:cursor-not-allowed disabled:opacity-50',
        isInvalid
          ? 'border-red-400 focus:border-red-500 focus:ring-red-300'
          : 'border-[hsl(var(--border))] focus:border-brand focus:ring-brand/40',
        className,
      )}
      {...props}
    />
  );
});
