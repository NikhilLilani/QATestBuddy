import { cn } from '@/lib/utils';

type Citation =
  | { kind: 'jira'; key: string; field: string }
  | { kind: 'rag_chunk'; chunk_id: string }
  | { kind: 'repo_file'; repo: string; path: string; line_start: number; line_end: number };

export function CitationPill({ citation, className }: { citation: Citation; className?: string }) {
  let label = '';
  let title = '';
  if (citation.kind === 'jira') {
    label = `${citation.key}·${citation.field}`;
    title = `Source: Jira ticket ${citation.key} (${citation.field})`;
  } else if (citation.kind === 'rag_chunk') {
    label = `doc·${citation.chunk_id.slice(0, 6)}`;
    title = `Source: RAG chunk ${citation.chunk_id}`;
  } else {
    label = `${citation.path}:${citation.line_start}`;
    title = `Source: ${citation.repo}/${citation.path}:${citation.line_start}-${citation.line_end}`;
  }
  return (
    <span
      title={title}
      className={cn(
        'inline-flex items-center rounded-md border border-brand/30 bg-brand/5 px-1.5 py-0.5 text-[10px] font-mono text-brand-700',
        className,
      )}
    >
      {label}
    </span>
  );
}

export function CitationList({ citations }: { citations: Citation[] }) {
  if (!citations || citations.length === 0) return null;
  return (
    <div className="mt-1 flex flex-wrap gap-1">
      {citations.map((c, i) => (
        <CitationPill key={i} citation={c} />
      ))}
    </div>
  );
}
