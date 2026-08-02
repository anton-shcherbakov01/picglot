import type { MetadataRoute } from 'next';

import { absoluteUrl } from '@/lib/i18n';

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: '*',
        allow: '/',
        // Private results, share links and filtered listings must never be
        // indexed; the pages also send X-Robots-Tag, this is belt and braces.
        disallow: [
          '/api/',
          '/*/app/',
          '/share/',
          '/*/auth/',
          '/*?*sort=',
          '/*?*filter=',
          '/*?*page=',
        ],
      },
    ],
    sitemap: absoluteUrl('/sitemap.xml'),
    host: absoluteUrl('/'),
  };
}
