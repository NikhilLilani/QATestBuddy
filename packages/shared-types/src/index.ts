import { z } from 'zod';

/* ------------------------------------------------------------------ */
/* Citation envelope — every agent output must include citations[]    */
/* ------------------------------------------------------------------ */

export const CitationSchema = z.discriminatedUnion('kind', [
  z.object({ kind: z.literal('jira'), key: z.string(), field: z.string() }),
  z.object({ kind: z.literal('rag_chunk'), chunk_id: z.string() }),
  z.object({
    kind: z.literal('repo_file'),
    repo: z.string(),
    path: z.string(),
    line_start: z.number().int(),
    line_end: z.number().int(),
  }),
]);
export type Citation = z.infer<typeof CitationSchema>;

export const AgentEnvelopeSchema = <T extends z.ZodTypeAny>(data: T) =>
  z.object({
    data,
    citations: z.array(CitationSchema).min(1),
    confidence: z.number().min(0).max(1),
    clarifications_used: z.number().int().min(0),
    tokens: z.object({ in: z.number().int(), out: z.number().int() }),
  });

/* ------------------------------------------------------------------ */
/* Domain entities                                                    */
/* ------------------------------------------------------------------ */

export const TestPlanSchema = z.object({
  scope: z.string(),
  in_scope: z.array(z.string()),
  out_of_scope: z.array(z.string()),
  risks: z.array(z.object({ risk: z.string(), mitigation: z.string() })),
  environments: z.array(z.string()),
  data_needs: z.array(z.string()),
  exit_criteria: z.array(z.string()),
});
export type TestPlan = z.infer<typeof TestPlanSchema>;

export const TestCaseSchema = z.object({
  id: z.string(),
  title: z.string(),
  preconditions: z.array(z.string()),
  steps: z.array(z.object({ action: z.string(), expected: z.string() })),
  priority: z.enum(['P0', 'P1', 'P2', 'P3']),
  type: z.enum(['functional', 'regression', 'edge', 'negative', 'a11y', 'perf']),
  data: z.record(z.unknown()).optional(),
  tags: z.array(z.string()),
});
export type TestCase = z.infer<typeof TestCaseSchema>;

export const BugReportSchema = z.object({
  title: z.string(),
  description: z.string(),
  severity: z.enum(['critical', 'high', 'medium', 'low']),
  file_refs: z.array(
    z.object({ path: z.string(), line_start: z.number().int(), line_end: z.number().int() }),
  ),
  reproduction: z.array(z.string()).optional(),
  trace_uri: z.string().optional(),
});
export type BugReport = z.infer<typeof BugReportSchema>;

/* ------------------------------------------------------------------ */
/* SSE event union                                                    */
/* ------------------------------------------------------------------ */

export const SSEEventSchema = z.discriminatedUnion('event', [
  z.object({ event: z.literal('token'), data: z.string() }),
  z.object({ event: z.literal('step'), data: z.object({ name: z.string(), status: z.string() }) }),
  z.object({
    event: z.literal('clarify'),
    data: z.object({ id: z.string(), question: z.string(), schema: z.unknown() }),
  }),
  z.object({ event: z.literal('result'), data: z.unknown() }),
  z.object({ event: z.literal('error'), data: z.object({ message: z.string() }) }),
  z.object({ event: z.literal('done'), data: z.object({}).passthrough() }),
]);
export type SSEEvent = z.infer<typeof SSEEventSchema>;
