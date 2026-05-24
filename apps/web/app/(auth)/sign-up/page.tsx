import Link from 'next/link';
import { brand } from '@qa/brand';
import { isSupabaseConfigured } from '@/lib/supabase/status';
import { SignUpForm } from './sign-up-form';

export const metadata = { title: 'Create your account' };

export default function SignUpPage() {
  const configured = isSupabaseConfigured();
  return (
    <div>
      <h1 className="text-2xl font-semibold">Create your {brand.name} account</h1>
      <p className="mt-2 text-sm text-[hsl(var(--muted-foreground))]">
        Free forever. Bring your own AI key.
      </p>
      <div className="mt-6">
        <SignUpForm configured={configured} />
      </div>
      <p className="mt-6 text-center text-sm text-[hsl(var(--muted-foreground))]">
        Already have an account?{' '}
        <Link href="/sign-in" className="font-medium text-brand hover:underline">
          Sign in
        </Link>
      </p>
    </div>
  );
}
