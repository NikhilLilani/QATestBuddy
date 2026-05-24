/**
 * Single source of truth for branding.
 *
 * To rebrand the product:
 *   1. Edit values below.
 *   2. Replace files in ./assets/ (logo-light.svg, logo-dark.svg, og.png, favicon.ico).
 *   3. Rename OAuth apps in Google / Microsoft / GitHub consoles.
 *
 * No code refactor required. Every UI string and OG/meta tag reads from here.
 */
export const brand = {
  name: 'QAtestbuddy',
  shortName: 'QAtb',
  tagline: 'Grounded AI for QA — plans, cases, and Playwright tests from your Jira.',
  description:
    'QAtestbuddy turns Jira tickets into test plans, test cases, and runnable Playwright automation. Every output is grounded in your PRDs, history, and code — with citations and a clarify loop instead of hallucinations.',
  domain: 'localhost:3000',
  url: 'http://localhost:3000',
  supportEmail: 'support@localhost',
  themeColor: '#0EA5E9',
  ogImagePath: '/og.png',
  logo: {
    light: '/brand/logo-light.svg',
    dark: '/brand/logo-dark.svg',
  },
  social: {
    twitter: '',
    github: '',
    linkedin: '',
  },
  legal: {
    company: 'QAtestbuddy',
    address: '',
  },
} as const;

export type Brand = typeof brand;
