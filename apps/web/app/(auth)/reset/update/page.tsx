import { UpdatePasswordForm } from './update-form';

export const metadata = { title: 'Set a new password' };

export default function UpdatePasswordPage() {
  return (
    <div>
      <h1 className="text-2xl font-semibold">Set a new password</h1>
      <p className="mt-2 text-sm text-[hsl(var(--muted-foreground))]">
        Choose a password of at least 8 characters.
      </p>
      <div className="mt-6">
        <UpdatePasswordForm />
      </div>
    </div>
  );
}
