import { Check } from 'lucide-react';
import { cn } from '@/lib/utils';

export type StepState = 'done' | 'current' | 'pending';

export interface Step {
  label: string;
  hint?: string;
  state: StepState;
}

export function WorkflowStepper({ steps }: { steps: Step[] }) {
  return (
    <ol className="flex w-full items-center gap-2 overflow-x-auto py-1">
      {steps.map((s, i) => {
        const isLast = i === steps.length - 1;
        return (
          <li key={s.label} className="flex flex-1 items-center gap-2">
            <div
              className={cn(
                'flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm transition-colors',
                s.state === 'done' && 'border-emerald-300 bg-emerald-50 text-emerald-800',
                s.state === 'current' && 'border-brand bg-brand/10 text-brand-700',
                s.state === 'pending' &&
                  'border-[hsl(var(--border))] bg-white text-[hsl(var(--muted-foreground))]',
              )}
            >
              <span
                className={cn(
                  'flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold',
                  s.state === 'done' && 'bg-emerald-500 text-white',
                  s.state === 'current' && 'bg-brand text-white',
                  s.state === 'pending' && 'bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))]',
                )}
              >
                {s.state === 'done' ? <Check className="h-3 w-3" /> : i + 1}
              </span>
              <div className="leading-tight">
                <div className="text-xs font-medium">{s.label}</div>
                {s.hint && (
                  <div
                    className={cn(
                      'text-[10px]',
                      s.state === 'current'
                        ? 'text-brand-700/80'
                        : 'text-[hsl(var(--muted-foreground))]',
                    )}
                  >
                    {s.hint}
                  </div>
                )}
              </div>
            </div>
            {!isLast && (
              <div
                className={cn(
                  'h-px flex-1 transition-colors',
                  s.state === 'done' ? 'bg-emerald-300' : 'bg-[hsl(var(--border))]',
                )}
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}
