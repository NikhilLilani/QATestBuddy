'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import type { Route } from 'next';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { validateJiraKey } from '@/lib/validation';

export function TicketKeyForm() {
  const router = useRouter();
  const [key, setKey] = useState('');
  const [touched, setTouched] = useState(false);
  const [pending, startTransition] = useTransition();

  const trimmed = key.trim().toUpperCase();
  const error = touched ? validateJiraKey(trimmed) : null;
  const canSubmit = !pending && !validateJiraKey(trimmed);

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        setTouched(true);
        if (!canSubmit) return;
        startTransition(() => router.push(`/tickets/${trimmed}` as Route));
      }}
      className="flex flex-wrap gap-2"
    >
      <div className="min-w-[220px] flex-1 space-y-1">
        <Label htmlFor="key" className="sr-only">
          Jira key
        </Label>
        <Input
          id="key"
          name="key"
          placeholder="PROJ-123"
          required
          autoComplete="off"
          autoFocus
          value={key}
          error={error ?? undefined}
          onChange={(e) => setKey(e.target.value)}
          onBlur={() => setTouched(true)}
        />
        {error && <p className="text-xs text-red-600">{error}</p>}
      </div>
      <Button type="submit" loading={pending} loadingText="Opening…" disabled={!canSubmit}>
        Open
      </Button>
    </form>
  );
}
