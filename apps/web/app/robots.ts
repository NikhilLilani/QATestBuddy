import type { MetadataRoute } from 'next';
import { brand } from '@qa/brand';

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: '*', allow: '/', disallow: ['/api/', '/workspaces/'] }],
    sitemap: `${brand.url}/sitemap.xml`,
  };
}
