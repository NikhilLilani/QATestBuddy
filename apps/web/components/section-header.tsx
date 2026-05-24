import type { ReactNode } from 'react';
import { CardHeader, CardTitle, CardDescription } from '@/components/ui/card';

/**
 * Consistent CardHeader pattern used across the app:
 *
 *   [icon tile]  Title  [optional badge]
 *                Description text…                 [actions]
 *
 * Drop into any <Card> as a direct child instead of <CardHeader>.
 */
export function SectionHeader({
  icon,
  title,
  description,
  badge,
  actions,
  tone = 'brand',
}: {
  icon: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  badge?: ReactNode;
  actions?: ReactNode;
  tone?: 'brand' | 'emerald' | 'amber' | 'sky' | 'violet';
}) {
  const toneClass =
    tone === 'emerald'
      ? 'bg-emerald-100 text-emerald-700'
      : tone === 'amber'
        ? 'bg-amber-100 text-amber-700'
        : tone === 'sky'
          ? 'bg-sky-100 text-sky-700'
          : tone === 'violet'
            ? 'bg-violet-100 text-violet-700'
            : 'bg-brand/10 text-brand';
  return (
    <CardHeader>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div
            className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md ${toneClass}`}
          >
            {icon}
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle>{title}</CardTitle>
              {badge}
            </div>
            {description && <CardDescription>{description}</CardDescription>}
          </div>
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
    </CardHeader>
  );
}
