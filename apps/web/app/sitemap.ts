import type { MetadataRoute } from 'next';
import { brand } from '@qa/brand';

export default function sitemap(): MetadataRoute.Sitemap {
  const base = brand.url;
  return [
    { url: `${base}/`, changeFrequency: 'weekly', priority: 1 },
    { url: `${base}/sign-in`, changeFrequency: 'monthly', priority: 0.5 },
    { url: `${base}/sign-up`, changeFrequency: 'monthly', priority: 0.5 },
  ];
}
