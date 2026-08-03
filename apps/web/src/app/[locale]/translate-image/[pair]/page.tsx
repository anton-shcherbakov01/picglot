import type { Metadata } from 'next';
import { notFound } from 'next/navigation';

import { WorkArea } from '@/components/WorkArea';
import { serverFetch, type AppConfig, type SeoPageResponse } from '@/lib/api';
import { absoluteUrl, alternates, isLocale, localePath, type Locale } from '@/lib/i18n';
import { getMessages } from '@/lib/messages';

/**
 * Language-pair landing pages (`/translate-image/english-to-russian`, etc).
 *
 * The seeder writes this content under `/translate-image/{source}-to-{target}`
 * (see `db/seed.py`, `kind="language_pair"`) — this route is what actually
 * serves it; the single-segment `[tool]` route can't reach a two-segment path.
 */

async function loadPair(locale: string, pair: string) {
  const path = `/translate-image/${pair}`;
  const [config, seo] = await Promise.all([
    serverFetch<AppConfig>('/api/v1/config', { revalidate: 600 }),
    serverFetch<SeoPageResponse>(
      `/api/v1/content/page?path=${encodeURIComponent(path)}&locale=${locale}`,
      { revalidate: 900 },
    ),
  ]);
  return { config, seo };
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string; pair: string }>;
}): Promise<Metadata> {
  const { locale, pair } = await params;
  if (!isLocale(locale)) return {};
  const { seo } = await loadPair(locale, pair);
  if (!seo) return {};

  const path = `translate-image/${pair}`;
  const { languages } = alternates(`/${path}`);
  return {
    title: seo.title,
    description: seo.description,
    alternates: { canonical: absoluteUrl(localePath(locale, path)), languages },
    robots: seo.noindex ? { index: false, follow: true } : undefined,
    openGraph: {
      title: seo.h1,
      description: seo.description,
      url: absoluteUrl(localePath(locale, path)),
    },
  };
}

export default async function LanguagePairPage({
  params,
}: {
  params: Promise<{ locale: string; pair: string }>;
}) {
  const { locale, pair } = await params;
  if (!isLocale(locale)) notFound();

  const typed = locale as Locale;
  const messages = getMessages(typed);
  const { config, seo } = await loadPair(locale, pair);
  if (!config || !seo) notFound();

  const toolSlug = seo.tool_slug ?? 'image-translator';
  const spec = config.tools.find((item) => item.slug === toolSlug);
  if (!spec) notFound();

  const structuredData = {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'BreadcrumbList',
        itemListElement: [
          { '@type': 'ListItem', position: 1, name: 'LingoImage AI', item: absoluteUrl(localePath(typed)) },
          { '@type': 'ListItem', position: 2, name: seo.h1, item: absoluteUrl(localePath(typed, `translate-image/${pair}`)) },
        ],
      },
      ...(seo.faq?.length
        ? [
            {
              '@type': 'FAQPage',
              mainEntity: seo.faq.map((item) => ({
                '@type': 'Question',
                name: item.q,
                acceptedAnswer: { '@type': 'Answer', text: item.a },
              })),
            },
          ]
        : []),
    ],
  };

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(structuredData) }}
      />

      <section className="container-page py-10 sm:py-14">
        <nav aria-label="Breadcrumb" className="mb-4 text-sm text-muted">
          <a href={localePath(typed)} className="hover:text-fg">
            LingoImage AI
          </a>
          <span aria-hidden> / </span>
          <span aria-current="page">{seo.h1}</span>
        </nav>

        <div className="mx-auto max-w-3xl text-center">
          <h1 className="text-3xl font-bold sm:text-4xl">{seo.h1}</h1>
          {seo.description && <p className="mt-3 text-muted">{seo.description}</p>}
        </div>

        <div className="mx-auto mt-8 max-w-3xl">
          <WorkArea
            locale={typed}
            messages={messages}
            config={config}
            toolSlug={spec.slug}
            defaultSource={seo.source_language}
            defaultTarget={seo.target_language}
          />
        </div>
      </section>

      {seo.intro && (
        <section className="container-page pb-8">
          <div className="prose-content mx-auto max-w-3xl">
            <p>{seo.intro}</p>
          </div>
        </section>
      )}

      {seo.faq && seo.faq.length > 0 && (
        <section className="container-page pb-16" aria-labelledby="pair-faq">
          <div className="mx-auto max-w-3xl">
            <h2 id="pair-faq" className="text-2xl font-semibold">
              {messages.home.faqHeading}
            </h2>
            <div className="mt-5 grid gap-3">
              {seo.faq.map((item) => (
                <details key={item.q} className="card p-4">
                  <summary className="cursor-pointer font-medium">{item.q}</summary>
                  <p className="mt-2 text-sm text-muted">{item.a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>
      )}
    </>
  );
}
