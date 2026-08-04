import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";

import { BatchArea } from "@/components/BatchArea";
import { WorkArea } from "@/components/WorkArea";
import { serverFetch, type AppConfig, type SeoPageResponse } from "@/lib/api";
import {
  absoluteUrl,
  alternates,
  isLocale,
  localePath,
  toolAlternates,
  toolSlugFor,
  type Locale,
} from "@/lib/i18n";
import { getMessages } from "@/lib/messages";
import { toolTitle } from "@/lib/tools";

/**
 * One page component serves every tool and every format landing page.
 *
 * The copy comes from the CMS (`seo_pages`) so marketing can edit it without a
 * deploy; the working tool is always rendered above the copy, so a visitor from
 * search lands directly on something usable rather than on an article.
 */

async function loadPage(locale: string, tool: string) {
  const [config, seo] = await Promise.all([
    serverFetch<AppConfig>("/api/v1/config", { revalidate: 600 }),
    serverFetch<SeoPageResponse>(
      `/api/v1/content/page?path=/${encodeURIComponent(tool)}&locale=${locale}`,
      { revalidate: 900 },
    ),
  ]);
  return { config, seo };
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string; tool: string }>;
}): Promise<Metadata> {
  const { locale, tool } = await params;
  if (!isLocale(locale)) return {};
  const { config, seo } = await loadPage(locale, tool);
  if (!seo) return {};

  // Follow each locale's own slug, so hreflang never points at a redirect.
  // True when `tool` is this tool's page in this locale, English or localised;
  // a format landing page maps to a tool but keeps one slug in every locale.
  const spec = config?.tools.find((item) => item.slug === seo.tool_slug);
  const isToolPage =
    spec && toolSlugFor(locale, spec.slug, spec.localized_slugs) === tool;
  const { languages } = isToolPage
    ? toolAlternates(spec.slug, spec.localized_slugs)
    : alternates(`/${tool}`);
  return {
    title: seo.title,
    description: seo.description,
    alternates: { canonical: absoluteUrl(localePath(locale, tool)), languages },
    robots: seo.noindex ? { index: false, follow: true } : undefined,
    openGraph: {
      title: seo.h1,
      description: seo.description,
      url: absoluteUrl(localePath(locale, tool)),
      images: [
        {
          url: absoluteUrl(`/og/${locale}/${tool}.png`),
          width: 1200,
          height: 630,
        },
      ],
    },
  };
}

export default async function ToolPage({
  params,
}: {
  params: Promise<{ locale: string; tool: string }>;
}) {
  const { locale, tool } = await params;
  if (!isLocale(locale)) notFound();

  const typed = locale as Locale;
  const messages = getMessages(typed);
  const { config, seo } = await loadPage(locale, tool);

  if (!config) notFound();

  // The slug is either a tool, or a format landing page that maps to a tool.
  const toolSlug = seo?.tool_slug ?? tool;
  const spec = config.tools.find((item) => item.slug === toolSlug);
  if (!spec) notFound();

  // This locale publishes the tool under its own slug: send the English path
  // there rather than serving the same page at two indexable addresses.
  const preferred = toolSlugFor(typed, spec.slug, spec.localized_slugs);
  if (tool === spec.slug && preferred !== tool) {
    redirect(localePath(typed, preferred));
  }

  const heading = seo?.h1 ?? toolTitle(spec.slug, typed);

  const structuredData = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "BreadcrumbList",
        itemListElement: [
          {
            "@type": "ListItem",
            position: 1,
            name: "PicGlot",
            item: absoluteUrl(localePath(typed)),
          },
          {
            "@type": "ListItem",
            position: 2,
            name: heading,
            item: absoluteUrl(localePath(typed, tool)),
          },
        ],
      },
      {
        "@type": "SoftwareApplication",
        name: heading,
        applicationCategory: "UtilitiesApplication",
        operatingSystem: "Any",
        offers: { "@type": "Offer", price: "0", priceCurrency: "USD" },
      },
      ...(seo?.faq?.length
        ? [
            {
              "@type": "FAQPage",
              mainEntity: seo.faq.map((item) => ({
                "@type": "Question",
                name: item.q,
                acceptedAnswer: { "@type": "Answer", text: item.a },
              })),
            },
          ]
        : []),
    ],
  };

  const formats =
    seo?.body_sections.find((section) => section.type === "formats")?.items ??
    spec.accepts;
  const exports =
    seo?.body_sections.find((section) => section.type === "exports")?.items ??
    spec.exports;

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(structuredData) }}
      />

      <section className="glow relative isolate container-page py-10 sm:py-16">
        <nav aria-label="Breadcrumb" className="meta mb-6">
          <a href={localePath(typed)} className="hover:text-fg">
            PicGlot
          </a>
          <span aria-hidden> / </span>
          <span aria-current="page">{heading}</span>
        </nav>

        <div className="mx-auto max-w-3xl text-center">
          <h1 className="text-title sm:text-display">{heading}</h1>
          {seo?.description && (
            <p className="mx-auto mt-4 max-w-2xl text-[17px] leading-relaxed text-muted">
              {seo.description}
            </p>
          )}
        </div>

        <div className="mx-auto mt-10 max-w-3xl">
          {spec.slug === "batch" ? (
            <BatchArea locale={typed} messages={messages} config={config} />
          ) : (
            <WorkArea
              locale={typed}
              messages={messages}
              config={config}
              toolSlug={spec.slug}
              defaultSource={seo?.source_language}
              defaultTarget={seo?.target_language}
            />
          )}
        </div>
      </section>

      {seo?.intro && (
        <section className="container-page pb-8">
          <div className="prose-content mx-auto max-w-3xl">
            <p>{seo.intro}</p>
          </div>
        </section>
      )}

      <section className="container-page pb-10">
        <div className="mx-auto grid max-w-3xl gap-px overflow-hidden rounded-card border border-border bg-border shadow-soft sm:grid-cols-2">
          <div className="bg-surface p-5">
            <h2 className="eyebrow">{messages.upload.formats}</h2>
            <p className="mt-2 font-mono text-[13px] uppercase tracking-[0.06em]">
              {formats.join(" · ")}
            </p>
          </div>
          <div className="bg-surface p-5">
            <h2 className="eyebrow">{messages.result.download}</h2>
            <p className="mt-2 font-mono text-[13px] uppercase tracking-[0.06em]">
              {exports.map((item) => item.replace("_", " ")).join(" · ")}
            </p>
          </div>
        </div>
      </section>

      {seo?.faq && seo.faq.length > 0 && (
        <section className="container-page pb-16" aria-labelledby="tool-faq">
          <div className="mx-auto max-w-3xl">
            <h2 id="tool-faq" className="text-2xl font-semibold">
              {messages.home.faqHeading}
            </h2>
            <div className="mt-5 grid gap-3">
              {seo.faq.map((item) => (
                <details key={item.q} className="card p-4">
                  <summary className="cursor-pointer font-medium">
                    {item.q}
                  </summary>
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
