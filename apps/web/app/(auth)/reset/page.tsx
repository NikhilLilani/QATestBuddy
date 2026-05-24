import { ResetRequestForm } from './reset-form';

export const metadata = { title: 'Reset your password' };

export default function ResetPage() {
  return (
    <div>
      <h1 className="text-2xl font-semibold">Reset your password</h1>
      <p className="mt-2 text-sm text-[hsl(var(--muted-foreground))]">
        We&apos;ll email you a 6-digit code to set a new password.
      </p>
      <div className="mt-6">
        <ResetRequestForm />
      </div>
    </div>
  );
}
