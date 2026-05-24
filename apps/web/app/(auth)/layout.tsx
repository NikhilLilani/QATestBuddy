import Link from 'next/link';
import { brand } from '@qa/brand';

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="container py-6">
        <Link href="/" className="text-lg font-semibold">
          {brand.name}
        </Link>
      </header>
      <main className="container flex flex-1 items-center justify-center pb-16">
        <div className="w-full max-w-sm">{children}</div>
      </main>
    </div>
  );
}
