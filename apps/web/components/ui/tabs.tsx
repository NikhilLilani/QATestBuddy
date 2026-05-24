'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { Route } from 'next';
import { cn } from '@/lib/utils';

export interface TabItem {
  href: Route;
  label: string;
}

export function NavTabs({ items, className }: { items: TabItem[]; className?: string }) {
  const pathname = usePathname();
  return (
    <nav className={cn('flex gap-1 border-b', className)}>
      {items.map((item) => {
        const active = pathname === item.href;
        return (
          <Link
            key={item.href}
            href={item.href}
            className={cn(
              'border-b-2 px-4 py-2 text-sm font-medium transition-colors',
              active
                ? 'border-brand text-[hsl(var(--foreground))]'
                : 'border-transparent text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))]',
            )}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
