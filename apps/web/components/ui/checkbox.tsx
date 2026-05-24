'use client';

import { forwardRef, useEffect, useRef, type InputHTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  /** When true, renders the indeterminate state (dash). Use for select-all headers. */
  indeterminate?: boolean;
}

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { className, indeterminate = false, ...props },
  forwarded,
) {
  const innerRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const node = innerRef.current;
    if (!node) return;
    node.indeterminate = indeterminate;
  }, [indeterminate, props.checked]);

  // Combine refs
  const setRefs = (el: HTMLInputElement | null) => {
    innerRef.current = el;
    if (typeof forwarded === 'function') forwarded(el);
    else if (forwarded) forwarded.current = el;
  };

  return (
    <input
      ref={setRefs}
      type="checkbox"
      className={cn(
        'h-4 w-4 cursor-pointer rounded border-[hsl(var(--border))] text-brand',
        'transition-colors focus:ring-2 focus:ring-brand focus:ring-offset-1',
        'disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    />
  );
});
