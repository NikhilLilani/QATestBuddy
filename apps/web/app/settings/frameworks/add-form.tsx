'use client';

import { useActionState, useState } from 'react';
import { addFramework } from './actions';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Alert } from '@/components/ui/alert';

type Source = 'git' | 'local' | 'zip' | 'new';

const SOURCE_OPTIONS: { value: Source; label: string; help: string }[] = [
  {
    value: 'git',
    label: 'Git URL',
    help: 'Public GitHub repo. We read README, package.json, and sample tests to match your conventions automatically.',
  },
  {
    value: 'local',
    label: 'Local path',
    help: 'Path on your machine. The web app cannot read it directly — the path is used for codegen import paths only. Use qa-bridge (coming) for auto-save.',
  },
  {
    value: 'zip',
    label: 'Upload .zip',
    help: 'Upload a zipped framework. The backend extracts it for inspection. (Indexing pipeline lands soon — for now the path is saved as a label.)',
  },
  {
    value: 'new',
    label: 'New from scratch',
    help: 'No existing source. Codegen will produce a complete spec file using your conventions notes.',
  },
];

export function AddFrameworkForm() {
  const [source, setSource] = useState<Source>('git');
  const [state, action, pending] = useActionState(addFramework, null);

  return (
    <form action={action} className="space-y-4">
      <input type="hidden" name="source_kind" value={source} />

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="name">Name</Label>
          <Input
            id="name"
            name="name"
            required
            placeholder="e.g. Web App POM"
            maxLength={80}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="language">Language</Label>
          <select
            id="language"
            name="language"
            className="h-10 w-full rounded-md border border-[hsl(var(--border))] bg-transparent px-3 text-sm"
            defaultValue="playwright_ts"
          >
            <option value="playwright_ts">Playwright (TypeScript)</option>
            <option value="cypress">Cypress</option>
            <option value="selenium">Selenium</option>
            <option value="other">Other</option>
          </select>
        </div>
      </div>

      <div className="space-y-2">
        <Label>Source (pick one)</Label>
        <div className="grid gap-2 sm:grid-cols-2">
          {SOURCE_OPTIONS.map((opt) => (
            <label
              key={opt.value}
              className={`cursor-pointer rounded-md border p-3 text-sm transition ${
                source === opt.value
                  ? 'border-brand bg-brand/5'
                  : 'border-[hsl(var(--border))] hover:bg-[hsl(var(--muted))]'
              }`}
            >
              <input
                type="radio"
                name="_source_radio"
                value={opt.value}
                checked={source === opt.value}
                onChange={() => setSource(opt.value)}
                className="mr-2 accent-brand"
              />
              <span className="font-medium">{opt.label}</span>
              <p className="mt-1 text-xs text-[hsl(var(--muted-foreground))]">{opt.help}</p>
            </label>
          ))}
        </div>
      </div>

      {/* Source-specific fields */}
      {source === 'git' && (
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="git_url">Git URL</Label>
            <Input
              id="git_url"
              name="git_url"
              type="url"
              required
              placeholder="https://github.com/owner/repo"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="git_branch">Branch (optional)</Label>
            <Input id="git_branch" name="git_branch" placeholder="main" />
          </div>
        </div>
      )}

      {source === 'local' && (
        <div className="space-y-1.5">
          <Label htmlFor="local_path">Local path</Label>
          <Input
            id="local_path"
            name="local_path"
            required
            placeholder="C:\Users\you\projects\my-app\tests"
          />
          <p className="text-xs text-[hsl(var(--muted-foreground))]">
            Stored for context only. Auto-save via qa-bridge lands later.
          </p>
        </div>
      )}

      {source === 'zip' && (
        <div className="space-y-1.5">
          <Label htmlFor="zip_storage_key">Zip storage key</Label>
          <Input id="zip_storage_key" name="zip_storage_key" placeholder="(upload first)" />
          <p className="text-xs text-[hsl(var(--muted-foreground))]">
            Upload UI ships in the next iteration. For now, paste a manually-uploaded key.
          </p>
        </div>
      )}

      <div className="space-y-1.5">
        <Label htmlFor="conventions">Conventions / hints (optional)</Label>
        <textarea
          id="conventions"
          name="conventions"
          rows={4}
          className="w-full rounded-md border border-[hsl(var(--border))] bg-transparent px-3 py-2 text-sm"
          placeholder="e.g. Use the LoginPage POM from pages/LoginPage.ts. Test user creds are in TEST_USER_EMAIL/PASSWORD env vars."
        />
      </div>

      {state && !state.ok && <Alert variant="error">{state.error}</Alert>}
      {state?.ok && <Alert variant="success">Framework saved.</Alert>}

      <Button type="submit" disabled={pending}>
        {pending ? 'Saving…' : 'Save framework'}
      </Button>
    </form>
  );
}
