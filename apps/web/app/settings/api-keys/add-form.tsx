'use client';

import { useActionState } from 'react';
import { addApiKey } from '../actions';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Alert } from '@/components/ui/alert';

const PROVIDERS = [
  { value: 'anthropic', label: 'Anthropic (Claude)' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'gemini', label: 'Google Gemini' },
  { value: 'openrouter', label: 'OpenRouter' },
];

export function AddApiKeyForm() {
  const [state, action, pending] = useActionState(addApiKey, null);

  return (
    <form action={action} className="grid gap-3 sm:grid-cols-2">
      <div className="space-y-1.5">
        <Label htmlFor="provider">Provider</Label>
        <select
          id="provider"
          name="provider"
          required
          className="h-10 w-full rounded-md border border-[hsl(var(--border))] bg-transparent px-3 text-sm"
        >
          {PROVIDERS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="name">Label</Label>
        <Input id="name" name="name" placeholder="e.g. work / personal" required />
      </div>
      <div className="sm:col-span-2 space-y-1.5">
        <Label htmlFor="key">API key</Label>
        <Input id="key" name="key" type="password" placeholder="sk-..." required />
      </div>
      {state && !state.ok && (
        <div className="sm:col-span-2">
          <Alert variant="error">{state.error}</Alert>
        </div>
      )}
      {state?.ok && (
        <div className="sm:col-span-2">
          <Alert variant="success">Key saved.</Alert>
        </div>
      )}
      <div className="sm:col-span-2">
        <Button type="submit" disabled={pending}>
          {pending ? 'Saving…' : 'Save key'}
        </Button>
      </div>
    </form>
  );
}
