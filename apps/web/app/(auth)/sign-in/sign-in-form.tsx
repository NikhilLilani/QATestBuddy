'use client';

import Link from 'next/link';
import { useActionState, useState } from 'react';
import { signInWithOAuth, signInWithPassword } from '../actions';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Alert } from '@/components/ui/alert';
import { validateEmail, validateRequired } from '@/lib/validation';

export function SignInForm({ configured }: { configured: boolean }) {
  const [state, formAction, pending] = useActionState(signInWithPassword, {} as { error?: string });
  const [email, setEmail] = useState('');
  const [emailTouched, setEmailTouched] = useState(false);
  const [password, setPassword] = useState('');

  const emailError = emailTouched ? validateEmail(email) : null;
  const canSubmit =
    configured && !pending && !validateEmail(email) && !validateRequired(password, 'Password');

  return (
    <div className="space-y-4">
      {!configured && (
        <Alert variant="info">
          Supabase isn&apos;t configured yet. Add{' '}
          <code className="font-mono text-xs">NEXT_PUBLIC_SUPABASE_URL</code> and{' '}
          <code className="font-mono text-xs">NEXT_PUBLIC_SUPABASE_ANON_KEY</code> to{' '}
          <code className="font-mono text-xs">apps/web/.env.local</code>. The forms below are
          rendered for preview only.
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
          <div className="flex items-center justify-between">
            <Label htmlFor="password">Password</Label>
            <Link href="/reset" className="text-xs text-brand hover:underline">
              Forgot?
            </Link>
          </div>
          <Input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        {state?.error && <Alert variant="error">{state.error}</Alert>}
        <Button
          type="submit"
          className="w-full"
          loading={pending}
          loadingText="Signing in…"
          disabled={!canSubmit}
        >
          Sign in
        </Button>
      </form>
    </div>
  );
}
