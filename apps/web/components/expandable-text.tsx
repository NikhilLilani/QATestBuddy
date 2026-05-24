'use client';

import { useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';

/**
 * Collapsible long-form text. Shows the first `previewLines` lines (plus a
 * fade gradient), with a Show more / Show less toggle.
 *
 * We render the text inside a `<div>` with `white-space: pre-wrap` so paragraph
 * breaks and ordered/unordered markdown-ish lines render naturally without
 * pulling in a heavy markdown renderer.
 */
export function ExpandableText({
  text,
  previewLines = 8,
}: {
  text: string;
  previewLines?: number;
}) {
  const [open, setOpen] = useState(false);
  const lines = text.split('\n');
  const isLong = lines.length > previewLines;
  const visible = open || !isLong ? text : lines.slice(0, previewLines).join('\n');

  return (
    <div>
      <div className="relative">
        <div className="whitespace-pre-wrap text-sm leading-relaxed text-[hsl(var(--foreground))]">
          {visible}
        </div>
        {!open && isLong && (
          <div
            className="pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-white to-transparent"
            aria-hidden
          />
        )}
      </div>
      {isLong && (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-brand hover:underline"
        >
          {open ? (
            <>
              Show less <ChevronUp className="h-3.5 w-3.5" />
            </>
          ) : (
            <>
              Show full description <ChevronDown className="h-3.5 w-3.5" />
            </>
          )}
        </button>
      )}
    </div>
  );
}
