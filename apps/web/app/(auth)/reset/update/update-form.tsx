'use client';

import { useActionState } from 'react';
import { updatePassword } from '../../actions';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Alert } from '@/components/ui/alert';

export function UpdatePasswordForm() {
  const [state, formAction, pending] = useActionState(updatePassword, {} as { error?: string });

  return (
    <form action={formAction} className="space-y-3">
      <div className="space-y-1.5">
        <Label htmlFor="password">New password</Label>
        <Input
          id="password"
          name="password"
          type="password"
          minLength={8}
          autoComplete="new-password"
          required
        />
      </div>
      {state?.error && <Alert variant="error">{state.error}</Alert>}
      <Button type="submit" className="w-full" disabled={pending}>
        {pending ? 'Saving…' : 'Save password'}
      </Button>
    </form>
  );
}
