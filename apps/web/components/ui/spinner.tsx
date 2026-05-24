import { cn } from '@/lib/utils';

export function Spinner({
  size = 16,
  className,
}: {
  size?: number;
  className?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      aria-label="Loading"
      role="status"
      className={cn('animate-spin-fast', className)}
    >
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.2" strokeWidth="3" />
      <path
        d="M12 2a10 10 0 0 1 10 10"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        'animate-shimmer rounded bg-gradient-to-r from-[hsl(var(--muted))]/40 via-[hsl(var(--muted))]/60 to-[hsl(var(--muted))]/40 bg-[length:400px_100%]',
        className,
      )}
    />
  );
}
