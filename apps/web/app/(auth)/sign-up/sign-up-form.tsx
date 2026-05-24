'use client';

import { useActionState, useState } from 'react';
import { signInWithOAuth, signUpWithPassword } from '../actions';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Alert } from '@/components/ui/alert';
import { passwordStrength, validateEmail, validatePassword } from '@/lib/validation';
import { cn } from '@/lib/utils';

export function SignUpForm({ configured }: { configured: boolean }) {
  const [state, formAction, pending] = useActionState(signUpWithPassword, {} as { error?: string });
  const [email, setEmail] = useState('');
  const [emailTouched, setEmailTouched] = useState(false);
  const [password, setPassword] = useState('');
  const [passwordTouched, setPasswordTouched] = useState(false);

  const emailError = emailTouched ? validateEmail(email) : null;
  const passwordError = passwordTouched ? validatePassword(password) : null;
  const strength = passwordStrength(password);

  const canSubmit =
    configured && !pending && !validateEmail(email) && !validatePassword(password);

  return (
    <div className="space-y-4">
      {!configured && (
        <Alert variant="info">
          Supabase isn&apos;t configured yet. Add{' '}
          <code className="font-mono text-xs">NEXT_PUBLIC_SUPABASE_URL</code> and{' '}
          <code className="font-mono text-xs">NEXT_PUBLIC_SUPABASE_ANON_KEY</code> to{' '}
          <code className="font-mono text-xs">apps/web/.env.local</code> to enable sign-up.
        </Alert>
      )}

      <div className="grid gap-2">
        <Button
          type="button"
          variant="secondary"
          disabled={!configured}
          onClick={() => signInWithOAuth('google')}
        >
          Continue with Google
        </Button>
        <Button
          type="button"
          variant="secondary"
          disabled={!configured}
          onClick={() => signInWithOAuth('azure')}
        >
          Continue with Microsoft
        </Button>
      </div>

      <div className="relative my-2">
        <div className="absolute inset-0 flex items-center">
          <span className="w-full border-t" />
        </div>
        <div className="relative flex justify-center text-xs uppercase">
          <span className="bg-[hsl(var(--background))] px-2 text-[hsl(var(--muted-foreground))]">
            or with email
          </span>
        </div>
      </div>

      <form action={formAction} className="space-y-3">
        <div className="space-y-1.5">
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            name="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            error={emailError ?? undefined}
            onChange={(e) => setEmail(e.target.value)}
            onBlur={() => setEmailTouched(true)}
          />
          {emailError && <p className="text-xs text-red-600">{emailError}</p>}
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            name="password"
            type="password"
            autoComplete="new-password"
            minLength={8}
            required
            value={password}
            error={passwordError ?? undefined}
            onChange={(e) => setPassword(e.target.value)}
            onBlur={() => setPasswordTouched(true)}
          />
          {password.length > 0 && (
            <div className="space-y-1">
              <div className="flex gap-1">
                {[0, 1, 2, 3].map((i) => (
                  <span
                    key={i}
                    className={cn(
                      'h-1 flex-1 rounded-full transition-colors',
                      i < strength.score
                        ? strength.score >= 3
                          ? 'bg-emerald-500'
                          : strength.score === 2
                            ? 'bg-amber-500'
                            : 'bg-red-400'
                        : 'bg-[hsl(var(--muted))]',
                    )}
                  />
                ))}
              </div>
              <p
                className={cn(
                  'text-xs',
                  passwordError ? 'text-red-600' : 'text-[hsl(var(--muted-foreground))]',
                )}
              >
                {passwordError ?? `Strength: ${strength.label}`}
              </p>
            </div>
          )}
          {!password.length && (
            <p className="text-xs text-[hsl(var(--muted-foreground))]">At least 8 characters.</p>
          )}
        </div>
        {state?.error && <Alert variant="error">{state.error}</Alert>}
        <Button
          type="submit"
          className="w-full"
          loading={pending}
          loadingText="Creating account…"
          disabled={!canSubmit}
        >
          Create account
        </Button>
      </form>
      <p className="text-center text-xs text-[hsl(var(--muted-foreground))]">
        We&apos;ll email you a 6-digit code to verify your address.
      </p>
    </div>
  );
}
