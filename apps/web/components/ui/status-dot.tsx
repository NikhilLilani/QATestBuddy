import { cn } from '@/lib/utils';

type Tone = 'green' | 'amber' | 'red' | 'sky' | 'gray';

const tones: Record<Tone, string> = {
  green: 'bg-emerald-500',
  amber: 'bg-amber-500',
  red: 'bg-red-500',
  sky: 'bg-sky-500',
  gray: 'bg-slate-400',
};

const rings: Record<Tone, string> = {
  green: 'ring-emerald-500/30',
  amber: 'ring-amber-500/30',
  red: 'ring-red-500/30',
  sky: 'ring-sky-500/30',
  gray: 'ring-slate-400/30',
};

export function StatusDot({ tone = 'gray', className }: { tone?: Tone; className?: string }) {
  return (
    <span
      className={cn(
        'inline-block h-2 w-2 rounded-full ring-4',
        tones[tone],
        rings[tone],
        className,
      )}
    />
  );
}

export function StatusBadge({
  tone = 'gray',
  children,
  icon,
}: {
  tone?: Tone;
  children: React.ReactNode;
  icon?: React.ReactNode;
}) {
  const ring: Record<Tone, string> = {
    green: 'border-emerald-200 bg-emerald-50 text-emerald-800',
    amber: 'border-amber-200 bg-amber-50 text-amber-800',
    red: 'border-red-200 bg-red-50 text-red-800',
    sky: 'border-sky-200 bg-sky-50 text-sky-800',
    gray: 'border-slate-200 bg-slate-50 text-slate-700',
  };
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium',
        ring[tone],
      )}
    >
      {icon}
      {children}
    </span>
  );
}
