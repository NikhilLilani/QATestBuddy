'use server';

import { redirect } from 'next/navigation';
import { revalidatePath } from 'next/cache';
import { createClient } from '@/lib/supabase/server';
import { brand } from '@qa/brand';

const APP_URL = process.env.NEXT_PUBLIC_APP_URL ?? brand.url;

type ActionResult = { error?: string; message?: string };

/* -------------------------------- sign in -------------------------------- */

export async function signInWithPassword(
  _prev: ActionResult,
  formData: FormData,
): Promise<ActionResult> {
  const email = String(formData.get('email') ?? '').trim();
  const password = String(formData.get('password') ?? '');
  if (!email || !password) return { error: 'Email and password are required.' };

  const supabase = await createClient();
  const { error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) return { error: error.message };

  revalidatePath('/', 'layout');
  redirect('/dashboard');
}

export async function signInWithOAuth(provider: 'google' | 'azure') {
  const supabase = await createClient();
  const { data, error } = await supabase.auth.signInWithOAuth({
    provider,
    options: {
      redirectTo: `${APP_URL}/auth/callback?next=/dashboard`,
      ...(provider === 'azure' ? { scopes: 'email openid profile' } : {}),
    },
  });
  if (error) return { error: error.message };
  // OAuth provider URL is external/dynamic — bypass typed-routes check.
  if (data?.url) redirect(data.url as never);
  return { error: 'No OAuth URL returned.' };
}

/* -------------------------------- sign up -------------------------------- */

export async function signUpWithPassword(
  _prev: ActionResult,
  formData: FormData,
): Promise<ActionResult> {
  const email = String(formData.get('email') ?? '').trim();
  const password = String(formData.get('password') ?? '');
  if (!email || !password) return { error: 'Email and password are required.' };
  if (password.length < 8) return { error: 'Password must be at least 8 characters.' };

  const supabase = await createClient();
  const { error } = await supabase.auth.signUp({
    email,
    password,
    options: { emailRedirectTo: `${APP_URL}/auth/callback?next=/dashboard` },
  });
  if (error) return { error: error.message };

  redirect(`/verify-otp?email=${encodeURIComponent(email)}&purpose=signup`);
}

export async function verifyOtp(_prev: ActionResult, formData: FormData): Promise<ActionResult> {
  const email = String(formData.get('email') ?? '').trim();
  const token = String(formData.get('token') ?? '').trim();
  const purpose = (String(formData.get('purpose') ?? 'signup') as 'signup' | 'recovery');
  if (!email || !token) return { error: 'Email and code are required.' };

  const supabase = await createClient();
  const { error } = await supabase.auth.verifyOtp({
    email,
    token,
    type: purpose === 'recovery' ? 'recovery' : 'email',
  });
  if (error) return { error: error.message };

  revalidatePath('/', 'layout');
  redirect(purpose === 'recovery' ? '/reset/update' : '/dashboard');
}

export async function resendOtp(_prev: ActionResult, formData: FormData): Promise<ActionResult> {
  const email = String(formData.get('email') ?? '').trim();
  if (!email) return { error: 'Email is required.' };

  const supabase = await createClient();
  const { error } = await supabase.auth.resend({ type: 'signup', email });
  if (error) return { error: error.message };
  return { message: 'New code sent.' };
}

/* ----------------------------- password reset ---------------------------- */

export async function requestPasswordReset(
  _prev: ActionResult,
  formData: FormData,
): Promise<ActionResult> {
  const email = String(formData.get('email') ?? '').trim();
  if (!email) return { error: 'Email is required.' };

  const supabase = await createClient();
  const { error } = await supabase.auth.resetPasswordForEmail(email, {
    redirectTo: `${APP_URL}/reset/update`,
  });
  if (error) return { error: error.message };
  return { message: 'If that email exists, a 6-digit code is on its way.' };
}

export async function updatePassword(
  _prev: ActionResult,
  formData: FormData,
): Promise<ActionResult> {
  const password = String(formData.get('password') ?? '');
  if (password.length < 8) return { error: 'Password must be at least 8 characters.' };

  const supabase = await createClient();
  const { error } = await supabase.auth.updateUser({ password });
  if (error) return { error: error.message };

  revalidatePath('/', 'layout');
  redirect('/dashboard');
}

/* -------------------------------- sign out ------------------------------- */

export async function signOut() {
  const supabase = await createClient();
  await supabase.auth.signOut();
  revalidatePath('/', 'layout');
  redirect('/sign-in');
}
