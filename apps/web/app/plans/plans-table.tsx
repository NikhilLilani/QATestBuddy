'use client';

import { useState, useTransition, useMemo } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { toast } from 'sonner';
import { Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { StatusBadge } from '@/components/ui/status-dot';
import { DeleteButton } from '@/components/delete-button';
import { bulkDeletePlansAction, deletePlanAction } from './actions';

export type PlanRow = {
  id: string;
  created_at: string;
  tokens_in: number;
  tokens_out: number;
  jira_key: string;
  title: string;
  status: string | null;
  case_count: number;
  run_count: number;
};

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString();
}

export function PlansTable({ plans }: { plans: PlanRow[] }) {
  const router = useRouter();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [pending, startTransition] = useTransition();

  const allChecked = plans.length > 0 && selected.size === plans.length;
  const someChecked = selected.size > 0 && !allChecked;

  const toggleOne = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    setSelected((prev) => (prev.size === plans.length ? new Set() : new Set(plans.map((p) => p.id))));
  };

  const ids = useMemo(() => Array.from(selected), [selected]);

  return (
    <div className="space-y-3">
      {/* Selection action bar */}
      {selected.size > 0 && (
        <div className="animate-fade-in flex flex-wrap items-center justify-between gap-2 rounded-md border border-brand/30 bg-brand/5 px-4 py-2 text-sm">
          <div>
            <strong>{selected.size}</strong> of {plans.length} selected
          </div>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())} disabled={pending}>
              Clear
            </Button>
            {confirming ? (
              <>
                <span className="text-xs text-red-700">
                  Delete {selected.size} plan{selected.size !== 1 ? 's' : ''} (and their cases)?
                </span>
                <Button
                  size="sm"
                  variant="danger"
                  loading={pending}
                  loadingText="Deleting…"
                  onClick={() =>
                    startTransition(async () => {
                      const r = await bulkDeletePlansAction(ids);
                      if (r.ok) {
                        toast.success(`Deleted ${r.data.deleted} plan(s).`);
                        setSelected(new Set());
                        setConfirming(false);
                        router.refresh();
                      } else {
                        toast.error(r.error);
                      }
                    })
                  }
                >
                  Yes, delete
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setConfirming(false)} disabled={pending}>
                  Cancel
                </Button>
              </>
            ) : (
              <Button size="sm" variant="danger" onClick={() => setConfirming(true)}>
                <Trash2 className="h-3.5 w-3.5" />
                Delete selected
              </Button>
            )}
          </div>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead className="bg-[hsl(var(--muted))]/40 text-left text-[11px] uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
            <tr>
              <th className="w-10 px-3 py-2">
                <Checkbox
                  checked={allChecked}
                  indeterminate={someChecked}
                  onChange={toggleAll}
                  aria-label="Select all plans"
                />
              </th>
              <th className="px-3 py-2">Ticket</th>
              <th className="px-3 py-2">Title</th>
              <th className="px-3 py-2">Cases</th>
              <th className="px-3 py-2">Runs</th>
              <th className="px-3 py-2">Tokens</th>
              <th className="px-3 py-2">Created</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {plans.map((p) => {
              const isSel = selected.has(p.id);
              return (
                <tr
                  key={p.id}
                  className={
                    isSel
                      ? 'bg-brand/5 hover:bg-brand/10'
                      : 'hover:bg-[hsl(var(--muted))]/30'
                  }
                >
                  <td className="px-3 py-2">
                    <Checkbox
                      checked={isSel}
                      onChange={() => toggleOne(p.id)}
                      aria-label={`Select ${p.jira_key}`}
                    />
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">{p.jira_key}</td>
                  <td className="px-3 py-2">
                    <div className="max-w-md truncate">{p.title}</div>
                    {p.status && <StatusBadge tone="gray">{p.status}</StatusBadge>}
                  </td>
                  <td className="px-3 py-2 text-xs">
                    <StatusBadge tone="sky">{p.case_count}</StatusBadge>
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {p.run_count > 0 ? (
                      <StatusBadge tone="green">{p.run_count}</StatusBadge>
                    ) : (
                      <span className="text-[hsl(var(--muted-foreground))]">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-xs text-[hsl(var(--muted-foreground))]">
                    {p.tokens_in} / {p.tokens_out}
                  </td>
                  <td className="px-3 py-2 text-xs text-[hsl(var(--muted-foreground))]">
                    {formatDate(p.created_at)}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-1">
                      <Link href={`/tickets/${p.jira_key}`}>
                        <Button size="sm" variant="secondary">
                          Open
                        </Button>
                      </Link>
                      <Link href={`/tickets/${p.jira_key}/codegen`}>
                        <Button size="sm">Codegen</Button>
                      </Link>
                      <DeleteButton
                        iconOnly
                        id={p.id}
                        action={deletePlanAction}
                        confirmLabel={`Delete plan for ${p.jira_key}?`}
                        successMessage={`Deleted plan for ${p.jira_key}.`}
                      />
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
