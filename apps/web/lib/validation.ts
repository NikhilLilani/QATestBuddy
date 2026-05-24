/**
 * Shared lightweight form validators.
 * Each returns `null` when valid, or a human-readable error string when invalid.
 */

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const JIRA_KEY_RE = /^[A-Za-z][A-Za-z0-9_]+-\d+$/;

export function validateEmail(value: string): string | null {
  const v = value.trim();
  if (!v) return 'Email is required.';
  if (!EMAIL_RE.test(v)) return 'Enter a valid email address.';
  return null;
}

export function validatePassword(value: string, opts: { min?: number } = {}): string | null {
  const min = opts.min ?? 8;
  if (!value) return 'Password is required.';
  if (value.length < min) return `Password must be at least ${min} characters.`;
  return null;
}

export function validateJiraKey(value: string): string | null {
  const v = value.trim();
  if (!v) return 'Ticket key is required.';
  if (!JIRA_KEY_RE.test(v)) return 'Use the format PROJECT-NUMBER (e.g. SCRUM-12).';
  return null;
}

export function validateRequired(value: string, label = 'This field'): string | null {
  return value.trim() ? null : `${label} is required.`;
}

export function validateMinLength(
  value: string,
  min: number,
  label = 'This field',
): string | null {
  return value.trim().length >= min ? null : `${label} must be at least ${min} characters.`;
}

export function validateUrl(value: string): string | null {
  const v = value.trim();
  if (!v) return 'URL is required.';
  try {
    const u = new URL(v);
    if (!u.protocol.startsWith('http')) return 'URL must start with http:// or https://';
    return null;
  } catch {
    return 'Enter a valid URL.';
  }
}

/** Password strength score 0-4 with a label. Useful for live feedback. */
export function passwordStrength(value: string): { score: 0 | 1 | 2 | 3 | 4; label: string } {
  let score = 0;
  if (value.length >= 8) score++;
  if (value.length >= 12) score++;
  if (/[A-Z]/.test(value) && /[a-z]/.test(value)) score++;
  if (/\d/.test(value) && /[^A-Za-z0-9]/.test(value)) score++;
  const labels = ['Too short', 'Weak', 'Okay', 'Strong', 'Very strong'];
  return { score: score as 0 | 1 | 2 | 3 | 4, label: labels[score] ?? 'Weak' };
}
