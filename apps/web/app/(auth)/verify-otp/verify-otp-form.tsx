'use client';

import { useActionState } from 'react';
import { resendOtp, verifyOtp } from '../actions';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Alert } from '@/components/ui/alert';

export function VerifyOtpForm({ email, purpose }: { email: string; purpose: 'signup' | 'recovery' }) {
  const [verifyState, verifyAction, verifyPending] = useActionState(verifyOtp, {} as { error?: string });
  const [resendState, resendAction, resendPending] = useActionState(resendOtp, {} as { error?: string; message?: string });

  return (
    <div className="space-y-4">
      <form action={verifyAction} className="space-y-3">
        <input type="hidden" name="email" value={email} />
        <input type="hidden" name="purpose" value={purpose} />
        <div className="space-y-1.5">
          <Label htmlFor="token">6-digit code</Label>
          <Input
            id="token"
            name="token"
            inputMode="numeric"
            pattern="[0-9]{6}"
            maxLength={6}
            autoComplete="one-time-code"
            required
            placeholder="123456"
          />
        </div>
        {verifyState?.error && <Alert variant="error">{verifyState.error}</Alert>}
        <Button type="submit" className="w-full" disabled={verifyPending}>
          {verifyPending ? 'Verifying…' : 'Verify'}
        </Button>
      </form>

      <form action={resendAction} className="text-center">
        <input type="hidden" name="email" value={email} />
        <Button type="submit" variant="ghost" size="sm" disabled={resendPending}>
          {resendPending ? 'Resending…' : "Didn't get it? Resend code"}
        </Button>
        {resendState?.message && <p className="mt-1 text-xs text-emerald-700">{resendState.message}</p>}
        {resendState?.error && <p className="mt-1 text-xs text-red-700">{resendState.error}</p>}
      </form>
    </div>
  );
}
