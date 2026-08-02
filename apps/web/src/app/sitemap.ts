import type { MetadataRoute } from 'next';

import { serverFetch } from '@/lib/api';
import { LOCALES, SITE_URL, absoluteUrl, localePath } from '@/lib/i18n';

interface SitemapData {
  pages: { path: string; locale: string; kind: string; updated_at: string }[];
  posts: { slug: string; locale: string; updated_at: string }[];
}

/**
 * Sitemap generated from the CMS, so a page cannot be published without being
 * discoverable. Pages marked `noindex` are excluded by the API query itself.
 */
export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const entries: MetadataRoute.Sitemap = [];

  for (const locale of LOCALES) {
    entries.push({
      url: absoluteUrl(localePath(locale)),
      lastModified: new Date(),
      changeFrequency: 'weekly',
      priority: 1,
      alternates: {
        languages: Object.fromEntries(
          LOCALES.map((alt) => [alt, absoluteUrl(localePath(alt))]),
        ),
      },
    });
    for (const path of ['pricing', 'api', 'supported-languages', 'security', 'blog']) {
      entries.push({
        url: absoluteUrl(localePath(locale, path)),
        lastModified: new Date(),
        changeFrequency: 'monthly',
        priority: 0.6,
      });
    }
  }

  const data = await serverFetch<SitemapData>('/api/v1/content/sitemap', {
    revalidate: 3600,
  });

  for (const page of data?.pages ?? []) {
    entries.push({
      url: absoluteUrl(localePath(page.locale as never, page.path)),
      lastModified: new Date(page.updated_at),
      changeFrequency: page.kind === 'tool' ? 'weekly' : 'monthly',
      priority: page.kind === 'tool' ? 0.9 : 0.7,
    });
  }

  for (const post of data?.posts ?? []) {
    entries.push({
      url: absoluteUrl(localePath(post.locale as never, `blog/${post.slug}`)),
      lastModified: new Date(post.updated_at),
      changeFrequency: 'yearly',
      priority: 0.5,
    });
  }

  return entries;
}

export const revalidate = 3600;

export { SITE_URL };
