import Link from 'next/link';
import { brand } from '@qa/brand';
import { isSupabaseConfigured } from '@/lib/supabase/status';
import { SignInForm } from './sign-in-form';

export const metadata = { title: 'Sign in' };

export default function SignInPage() {
  const configured = isSupabaseConfigured();
  return (
    <div>
      <h1 className="text-2xl font-semibold">Sign in to {brand.name}</h1>
      <p className="mt-2 text-sm text-[hsl(var(--muted-foreground))]">
        Use Google, Microsoft, or your email.
      </p>
      <div className="mt-6">
        <SignInForm configured={configured} />
      </div>
      <p className="mt-6 text-center text-sm text-[hsl(var(--muted-foreground))]">
        New here?{' '}
        <Link href="/sign-up" className="font-medium text-brand hover:underline">
          Create an account
        </Link>
      </p>
    </div>
  );
}
