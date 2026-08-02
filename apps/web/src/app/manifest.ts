import type { MetadataRoute } from 'next';

const BRAND = process.env.NEXT_PUBLIC_BRAND_NAME ?? 'LingoImage AI';

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: BRAND,
    short_name: 'LingoImage',
    description: 'Translate and convert text in images and documents.',
    start_url: '/',
    scope: '/',
    display: 'standalone',
    orientation: 'any',
    background_color: '#fafafb',
    theme_color: '#1f6feb',
    categories: ['productivity', 'utilities'],
    icons: [
      { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
      { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
      { src: '/icons/maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
    ],
    shortcuts: [
      { name: 'Translate an image', url: '/en/image-translator' },
      { name: 'Image to text', url: '/en/image-to-text' },
      { name: 'My projects', url: '/en/app' },
    ],
  };
}
