'use client';

import { useTransition } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { updateBugStatusAction } from '../actions';

export function StatusToggleButton({ id, status }: { id: string; status: string }) {
  const [pending, startTransition] = useTransition();
  const next = status === 'resolved' ? 'open' : 'resolved';

  return (
    <Button
      size="sm"
      variant={status === 'resolved' ? 'secondary' : 'primary'}
      loading={pending}
      loadingText="Updating…"
      onClick={() =>
        startTransition(async () => {
          const r = await updateBugStatusAction(id, next);
          if (r.ok) {
            toast.success(next === 'resolved' ? 'Marked resolved.' : 'Reopened.');
          } else {
            toast.error(r.error);
          }
        })
      }
    >
      {status === 'resolved' ? 'Reopen' : 'Mark resolved'}
    </Button>
  );
}
