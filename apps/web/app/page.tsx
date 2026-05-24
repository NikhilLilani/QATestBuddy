import Link from 'next/link';
import { brand } from '@qa/brand';

export default function LandingPage() {
  return (
    <main className="min-h-screen">
      <header className="container flex items-center justify-between py-6">
        <Link href="/" className="text-xl font-semibold">
          {brand.name}
        </Link>
        <nav className="flex items-center gap-4 text-sm">
          <Link href="#how" className="hover:underline">
            How it works
          </Link>
          <Link href="#features" className="hover:underline">
            Features
          </Link>
          <Link href="/sign-in" className="hover:underline">
            Sign in
          </Link>
          <Link
            href="/sign-up"
            className="rounded-md bg-brand px-3 py-1.5 text-white hover:bg-brand-600"
          >
            Get started
          </Link>
        </nav>
      </header>

      <section className="container py-24 text-center">
        <h1 className="mx-auto max-w-3xl text-5xl font-bold tracking-tight">
          {brand.tagline}
        </h1>
        <p className="mx-auto mt-6 max-w-2xl text-lg text-[hsl(var(--muted-foreground))]">
          {brand.description}
        </p>
        <div className="mt-10 flex justify-center gap-3">
          <Link
            href="/sign-up"
            className="rounded-md bg-brand px-5 py-3 font-medium text-white hover:bg-brand-600"
          >
            Start free
          </Link>
          <Link
            href="#how"
            className="rounded-md border px-5 py-3 font-medium hover:bg-[hsl(var(--muted))]"
          >
            See how it works
          </Link>
        </div>
      </section>

      <section id="how" className="container py-16">
        <h2 className="text-3xl font-bold">How it works</h2>
        <ol className="mt-8 grid gap-6 md:grid-cols-3">
          {[
            ['1. Connect', 'Link Jira and GitHub. Add API keys for your preferred LLM.'],
            ['2. Generate', 'Pick a ticket — get a test plan and cases grounded in your docs.'],
            ['3. Automate', 'Generate Playwright tests, run locally via qa-bridge, file bugs.'],
          ].map(([title, body]) => (
            <li key={title} className="rounded-lg border p-6">
              <h3 className="font-semibold">{title}</h3>
              <p className="mt-2 text-sm text-[hsl(var(--muted-foreground))]">{body}</p>
            </li>
          ))}
        </ol>
      </section>

      <footer className="container border-t py-10 text-sm text-[hsl(var(--muted-foreground))]">
        © {new Date().getFullYear()} {brand.legal.company}. Free forever — bring your own AI key.
      </footer>
    </main>
  );
}
