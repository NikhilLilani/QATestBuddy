import { forwardRef, type ButtonHTMLAttributes } from 'react';
import { cn } from '@/lib/utils';
import { Spinner } from './spinner';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
type Size = 'sm' | 'md' | 'lg';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  /** Show a spinner + disable the button while true. */
  loading?: boolean;
  /** Optional label shown while loading; defaults to current children. */
  loadingText?: string;
}

const variantClass: Record<Variant, string> = {
  primary:
    'bg-brand text-white shadow-sm hover:bg-brand-600 active:scale-[0.98] focus-visible:ring-brand',
  secondary:
    'border border-[hsl(var(--border))] bg-transparent hover:bg-[hsl(var(--muted))] active:scale-[0.98] focus-visible:ring-brand',
  ghost:
    'hover:bg-[hsl(var(--muted))] active:scale-[0.98] focus-visible:ring-brand',
  danger:
    'bg-red-600 text-white shadow-sm hover:bg-red-700 active:scale-[0.98] focus-visible:ring-red-500',
};

const sizeClass: Record<Size, string> = {
  sm: 'h-8 px-3 text-sm',
  md: 'h-10 px-4 text-sm',
  lg: 'h-11 px-5 text-base',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    className,
    variant = 'primary',
    size = 'md',
    loading = false,
    loadingText,
    disabled,
    children,
    ...props
  },
  ref,
) {
  const isDisabled = disabled || loading;
  return (
    <button
      ref={ref}
      disabled={isDisabled}
      aria-busy={loading || undefined}
      className={cn(
        'relative inline-flex items-center justify-center gap-2 rounded-md font-medium',
        'transition-[background-color,box-shadow,transform,opacity] duration-150 ease-out',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2',
        'disabled:cursor-not-allowed disabled:opacity-60 disabled:active:scale-100',
        variantClass[variant],
        sizeClass[size],
        className,
      )}
      {...props}
    >
      {loading && (
        <Spinner size={size === 'lg' ? 18 : 14} className="-ml-0.5" />
      )}
      {loading && loadingText ? loadingText : children}
    </button>
  );
});
