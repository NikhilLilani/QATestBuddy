'use client';

import { useTransition } from 'react';
import { toast } from 'sonner';
import { deleteFramework } from './actions';
import { Button } from '@/components/ui/button';

type Framework = {
  id: string;
  name: string;
  language: string;
  source_kind: 'git' | 'local' | 'zip' | 'new';
  git_url: string | null;
  git_branch: string | null;
  local_path: string | null;
  zip_storage_key: string | null;
};

export function FrameworksList({ items }: { items: Framework[] }) {
  const [pending, startTransition] = useTransition();

  if (items.length === 0) {
    return (
      <p className="text-sm text-[hsl(var(--muted-foreground))]">
        No frameworks yet. Add one above — Git URL is the fastest start.
      </p>
    );
  }

  return (
    <ul className="divide-y">
      {items.map((f) => (
        <li key={f.id} className="flex items-center justify-between py-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 text-sm">
              <span className="font-medium">{f.name}</span>
              <span className="rounded bg-[hsl(var(--muted))] px-1.5 py-0.5 text-[10px] font-mono">
                {f.language}
              </span>
              <span className="rounded bg-brand/10 px-1.5 py-0.5 text-[10px] font-mono text-brand-700">
                {f.source_kind}
              </span>
            </div>
            <div className="mt-0.5 truncate text-xs text-[hsl(var(--muted-foreground))]">
              {f.source_kind === 'git' && (
                <span>
                  {f.git_url}
                  {f.git_branch ? ` @ ${f.git_branch}` : ''}
                </span>
              )}
              {f.source_kind === 'local' && <span className="font-mono">{f.local_path}</span>}
              {f.source_kind === 'zip' && <span>zip: {f.zip_storage_key}</span>}
              {f.source_kind === 'new' && <span>Scaffolds a new framework</span>}
            </div>
          </div>
          <Button
            size="sm"
            variant="danger"
            disabled={pending}
            onClick={() =>
              startTransition(async () => {
                if (!confirm(`Delete framework "${f.name}"?`)) return;
                const r = await deleteFramework(f.id);
                if (!r.ok) toast.error(r.error);
                else toast.success('Deleted.');
              })
            }
          >
            Delete
          </Button>
        </li>
      ))}
    </ul>
  );
}
