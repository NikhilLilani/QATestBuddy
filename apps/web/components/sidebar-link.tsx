'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { Route } from 'next';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

export function SidebarLink({
  href,
  label,
  icon,
  exact = false,
}: {
  href: Route | string;
  label: string;
  icon?: ReactNode;
  exact?: boolean;
}) {
  const pathname = usePathname();
  // Exact match unless `exact=false`, in which case prefix match (handles nested routes)
  const active = exact ? pathname === href : pathname === href || pathname.startsWith(href + '/');
  return (
    <Link
      href={href as Route}
      className={cn(
        'group flex items-center justify-between rounded-md px-3 py-2 text-sm font-medium transition-colors',
        active
          ? 'bg-brand/10 text-brand-700'
          : 'text-[hsl(var(--muted-foreground))] hover:bg-[hsl(var(--muted))]/60 hover:text-[hsl(var(--foreground))]',
      )}
    >
      <span className="flex items-center gap-2.5">
        {icon && (
          <span
            className={cn(
              'flex h-4 w-4 shrink-0 items-center justify-center transition-colors',
              active ? 'text-brand' : 'text-[hsl(var(--muted-foreground))] group-hover:text-[hsl(var(--foreground))]',
            )}
            aria-hidden
          >
            {icon}
          </span>
        )}
        <span>{label}</span>
      </span>
      {active && <span className="h-1.5 w-1.5 rounded-full bg-brand" />}
    </Link>
  );
}
