import Link from 'next/link';
import { brand } from '@qa/brand';
import { signOut } from '@/app/(auth)/actions';
import { Button } from '@/components/ui/button';
import { StatusDot } from '@/components/ui/status-dot';
import { SidebarLink } from '@/components/sidebar-link';
import {
  LogOut,
  LayoutDashboard,
  Ticket,
  ClipboardList,
  PlayCircle,
  Boxes,
  Plug,
  KeyRound,
  TerminalSquare,
  User as UserIcon,
  Sparkles,
  Bug,
} from 'lucide-react';

export interface AppShellProps {
  user: { email?: string | null; id?: string };
  workspaceName?: string | null;
  /** Breadcrumb segments — e.g. ['Tickets', 'SCRUM-1', 'Generate automation'] */
  breadcrumb?: string[];
  /** Page title shown below breadcrumb */
  title?: string;
  /** Right-aligned action buttons in the header row */
  actions?: React.ReactNode;
  /** Sub-description under the title */
  subtitle?: string;
  children: React.ReactNode;
}

export function AppShell({
  user,
  workspaceName,
  breadcrumb,
  title,
  subtitle,
  actions,
  children,
}: AppShellProps) {
  return (
    <div className="flex min-h-screen bg-[hsl(var(--muted))]/30">
      {/* ──────────── Sidebar ──────────── */}
      <aside className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col border-r bg-white md:flex">
        <div className="flex h-16 items-center gap-2 border-b px-5">
          <Link href="/dashboard" className="flex items-baseline gap-2">
            <span className="text-lg font-semibold tracking-tight">{brand.name}</span>
            <span className="rounded bg-brand/10 px-1.5 py-0.5 text-[10px] font-mono uppercase text-brand-700">
              beta
            </span>
          </Link>
        </div>

        {workspaceName && (
          <div className="border-b px-3 py-3">
            <button
              type="button"
              className="flex w-full items-center gap-2 rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--muted))]/40 px-2.5 py-2 text-left transition-colors hover:bg-[hsl(var(--muted))]/70"
              title={workspaceName}
            >
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-brand text-[11px] font-semibold uppercase text-white">
                {workspaceName.slice(0, 1)}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-medium leading-tight">
                  {workspaceName}
                </span>
                <span className="flex items-center gap-1 text-[10px] text-[hsl(var(--muted-foreground))]">
                  <StatusDot tone="green" />
                  Active
                </span>
              </span>
            </button>
          </div>
        )}

        <nav className="flex-1 overflow-y-auto px-3 py-4 text-sm">
          <NavSection label="Workflow">
            <SidebarLink
              href="/dashboard"
              label="Dashboard"
              icon={<LayoutDashboard className="h-4 w-4" />}
              exact
            />
            <SidebarLink
              href="/tickets"
              label="Tickets"
              icon={<Ticket className="h-4 w-4" />}
            />
            <SidebarLink
              href="/automation/new"
              label="Direct automation"
              icon={<Sparkles className="h-4 w-4" />}
            />
            <SidebarLink
              href="/plans"
              label="Plans"
              icon={<ClipboardList className="h-4 w-4" />}
            />
            <SidebarLink
              href="/runs"
              label="Runs"
              icon={<PlayCircle className="h-4 w-4" />}
            />
            <SidebarLink
              href="/bugs"
              label="Bugs"
              icon={<Bug className="h-4 w-4" />}
            />
          </NavSection>

          <NavSection label="Setup">
            <SidebarLink
              href="/settings/frameworks"
              label="Frameworks"
              icon={<Boxes className="h-4 w-4" />}
            />
            <SidebarLink
              href="/settings/integrations"
              label="Integrations"
              icon={<Plug className="h-4 w-4" />}
            />
            <SidebarLink
              href="/settings/api-keys"
              label="API keys"
              icon={<KeyRound className="h-4 w-4" />}
            />
            <SidebarLink
              href="/settings/bridge"
              label="qa-bridge"
              icon={<TerminalSquare className="h-4 w-4" />}
            />
          </NavSection>

          <NavSection label="Account">
            <SidebarLink
              href="/settings"
              label="Profile"
              icon={<UserIcon className="h-4 w-4" />}
              exact
            />
          </NavSection>
        </nav>

        <div className="border-t px-3 py-3">
          <div className="flex items-center gap-3 rounded-md px-2 py-2">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand text-xs font-semibold uppercase text-white">
              {(user.email ?? '?').slice(0, 1)}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs font-medium">{user.email ?? 'unknown'}</div>
              <div className="text-[10px] text-[hsl(var(--muted-foreground))]">Signed in</div>
            </div>
            <form action={signOut}>
              <Button
                type="submit"
                variant="ghost"
                size="sm"
                title="Sign out"
                aria-label="Sign out"
                className="px-2"
              >
                <LogOut className="h-4 w-4" />
              </Button>
            </form>
          </div>
        </div>
      </aside>

      {/* ──────────── Main content ──────────── */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Mobile top bar (sidebar is hidden) */}
        <div className="flex h-14 items-center justify-between border-b bg-white px-4 md:hidden">
          <Link href="/dashboard" className="text-base font-semibold">
            {brand.name}
          </Link>
          <form action={signOut}>
            <Button type="submit" variant="ghost" size="sm">
              Sign out
            </Button>
          </form>
        </div>

        {/* Page header */}
        {(breadcrumb || title || actions) && (
          <header className="border-b bg-white">
            <div className="mx-auto max-w-6xl px-6 py-5">
              {breadcrumb && breadcrumb.length > 0 && (
                <nav className="mb-2 flex items-center gap-1.5 text-xs text-[hsl(var(--muted-foreground))]">
                  {breadcrumb.map((seg, i) => (
                    <span key={i} className="flex items-center gap-1.5">
                      {i > 0 && <span className="opacity-50">/</span>}
                      <span
                        className={
                          i === breadcrumb.length - 1
                            ? 'text-[hsl(var(--foreground))]'
                            : ''
                        }
                      >
                        {seg}
                      </span>
                    </span>
                  ))}
                </nav>
              )}
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  {title && (
                    <h1 className="truncate text-xl font-semibold tracking-tight">{title}</h1>
                  )}
                  {subtitle && (
                    <p className="mt-1 max-w-2xl text-sm text-[hsl(var(--muted-foreground))]">
                      {subtitle}
                    </p>
                  )}
                </div>
                {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
              </div>
            </div>
          </header>
        )}

        {/* Page body */}
        <main className="flex-1">
          <div className="mx-auto max-w-6xl px-6 py-6">{children}</div>
        </main>
      </div>
    </div>
  );
}

function NavSection({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-4">
      <div className="mb-1 px-3 text-[10px] font-medium uppercase tracking-[0.08em] text-[hsl(var(--muted-foreground))]/70">
        {label}
      </div>
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}
