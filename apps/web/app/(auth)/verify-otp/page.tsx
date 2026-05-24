import { brand } from '@qa/brand';
import { VerifyOtpForm } from './verify-otp-form';

export const metadata = { title: 'Verify your email' };

export default async function VerifyOtpPage({
  searchParams,
}: {
  searchParams: Promise<{ email?: string; purpose?: 'signup' | 'recovery' }>;
}) {
  const sp = await searchParams;
  const email = sp.email ?? '';
  const purpose = sp.purpose ?? 'signup';

  return (
    <div>
      <h1 className="text-2xl font-semibold">Check your email</h1>
      <p className="mt-2 text-sm text-[hsl(var(--muted-foreground))]">
        Enter the 6-digit code we sent to <strong>{email || 'your address'}</strong> to
        {purpose === 'recovery' ? ' reset your password' : ` finish creating your ${brand.name} account`}.
      </p>
      <div className="mt-6">
        <VerifyOtpForm email={email} purpose={purpose} />
      </div>
    </div>
  );
}
