'use client';

import { useState, useTransition } from 'react';
import { toast } from 'sonner';
import { Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

type Result = { ok: true } | { ok: false; error: string };

export function DeleteButton({
  id,
  action,
  confirmLabel,
  successMessage,
  className,
  size = 'sm',
  iconOnly = false,
  label = 'Delete',
}: {
  id: string;
  action: (id: string) => Promise<Result>;
  confirmLabel: string;
  successMessage: string;
  className?: string;
  size?: 'sm' | 'md';
  iconOnly?: boolean;
  label?: string;
}) {
  const [confirming, setConfirming] = useState(false);
  const [pending, startTransition] = useTransition();

  if (confirming) {
    return (
      <div className={cn('flex items-center gap-1.5', className)}>
        <span className="text-xs font-medium text-red-700">{confirmLabel}</span>
        <Button
          size="sm"
          variant="danger"
          loading={pending}
          loadingText="Deleting…"
          onClick={() =>
            startTransition(async () => {
              const r = await action(id);
              if (r.ok) {
                toast.success(successMessage);
                setConfirming(false);
              } else {
                toast.error(r.error);
              }
            })
          }
        >
          Yes
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={pending}
          onClick={() => setConfirming(false)}
        >
          Cancel
        </Button>
      </div>
    );
  }

  return (
    <Button
      size={size}
      variant="ghost"
      className={cn('text-red-600 hover:bg-red-50 hover:text-red-700', className)}
      onClick={() => setConfirming(true)}
      title={label}
      aria-label={label}
    >
      <Trash2 className="h-4 w-4" />
      {!iconOnly && <span>{label}</span>}
    </Button>
  );
}
