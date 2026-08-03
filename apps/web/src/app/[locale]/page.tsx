import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { toolTitle } from "@/components/Header";
import { WorkArea } from "@/components/WorkArea";
import { serverFetch, type AppConfig, type SeoPageResponse } from "@/lib/api";
import {
  absoluteUrl,
  alternates,
  isLocale,
  localePath,
  toolSlugFor,
  type Locale,
} from "@/lib/i18n";
import { getMessages } from "@/lib/messages";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const messages = getMessages(locale);
  const { languages } = alternates("/");

  return {
    title: `${messages.home.h1} — PicGlot`,
    description: messages.home.subtitle,
    alternates: { canonical: absoluteUrl(localePath(locale)), languages },
    openGraph: {
      title: messages.home.h1,
      description: messages.home.subtitle,
      url: absoluteUrl(localePath(locale)),
      images: [
        {
          url: absoluteUrl(`/og/${locale}/home.png`),
          width: 1200,
          height: 630,
        },
      ],
    },
  };
}

export default async function HomePage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const typed = locale as Locale;
  const messages = getMessages(typed);

  const config = await serverFetch<AppConfig>("/api/v1/config", {
    revalidate: 600,
  });
  const seo = await serverFetch<SeoPageResponse>(
    `/api/v1/content/page?path=/image-translator&locale=${locale}`,
    { revalidate: 900 },
  );

  if (!config) {
    return (
      <div className="container-page py-20 text-center">
        <h1 className="text-2xl font-semibold">
          {messages.errors.maintenance}
        </h1>
      </div>
    );
  }

  const faq = seo?.faq ?? [];

  const structuredData = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Organization",
        "@id": absoluteUrl("/#organization"),
        name: "PicGlot",
        url: absoluteUrl("/"),
      },
      {
        "@type": "WebApplication",
        name: "PicGlot",
        applicationCategory: "UtilitiesApplication",
        operatingSystem: "Any",
        url: absoluteUrl(localePath(typed)),
        description: messages.home.subtitle,
        offers: {
          "@type": "Offer",
          price: "0",
          priceCurrency: "USD",
          description: "Free tier with monthly credits",
        },
      },
      ...(faq.length
        ? [
            {
              "@type": "FAQPage",
              mainEntity: faq.map((item) => ({
                "@type": "Question",
                name: item.q,
                acceptedAnswer: { "@type": "Answer", text: item.a },
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

      {/* Hero — the upload zone is on the first screen, not below a wall of copy. */}
      <section className="container-page pb-10 pt-10 sm:pt-16">
        <div className="mx-auto max-w-3xl text-center">
          <h1 className="text-3xl font-bold sm:text-5xl">{messages.home.h1}</h1>
          <p className="mx-auto mt-4 max-w-2xl text-base text-muted sm:text-lg">
            {messages.home.subtitle}
          </p>
          <div className="mt-4 flex flex-wrap items-center justify-center gap-2 text-xs text-muted">
            <span className="chip">✓ {messages.home.trustNoSignup}</span>
            <span className="chip">✓ {messages.home.trustDeleted}</span>
            <span className="chip">✓ {messages.home.trustFormats}</span>
          </div>
        </div>

        <div className="mx-auto mt-8 max-w-3xl">
          <WorkArea
            locale={typed}
            messages={messages}
            config={config}
            toolSlug="image-translator"
          />
        </div>
      </section>

      <section className="container-page py-12" aria-labelledby="tools-heading">
        <h2 id="tools-heading" className="text-2xl font-semibold">
          {messages.home.toolsHeading}
        </h2>
        <p className="mt-1 text-muted">{messages.home.toolsSubtitle}</p>
        <ul className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {config.tools.map((tool) => (
            <li key={tool.slug}>
              <Link
                href={localePath(
                  typed,
                  toolSlugFor(typed, tool.slug, tool.localized_slugs),
                )}
                className="block h-full rounded-card border border-border bg-surface p-4 transition-colors hover:border-accent"
              >
                <span className="font-semibold">
                  {toolTitle(tool.slug, typed)}
                </span>
                <span className="mt-1 block text-sm text-muted">
                  {tool.accepts
                    .slice(0, 5)
                    .map((ext) => ext.toUpperCase())
                    .join(", ")}{" "}
                  →{" "}
                  {tool.exports
                    .slice(0, 3)
                    .map((ext) => ext.toUpperCase())
                    .join(", ")}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </section>

      <section className="container-page py-12" aria-labelledby="how-heading">
        <h2 id="how-heading" className="text-2xl font-semibold">
          {messages.home.howHeading}
        </h2>
        <ol className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {messages.home.howSteps.map((step, index) => (
            <li key={step.title} className="card p-5">
              <span
                aria-hidden
                className="mb-3 grid h-8 w-8 place-items-center rounded-full bg-accent/10 text-sm font-bold text-accent"
              >
                {index + 1}
              </span>
              <h3 className="font-semibold">{step.title}</h3>
              <p className="mt-1 text-sm text-muted">{step.body}</p>
            </li>
          ))}
        </ol>
      </section>

      <section
        className="container-page py-12"
        aria-labelledby="security-heading"
      >
        <div className="card p-6 sm:p-8">
          <h2 id="security-heading" className="text-2xl font-semibold">
            {messages.home.securityHeading}
          </h2>
          <ul className="mt-4 grid gap-3 sm:grid-cols-2">
            {messages.home.securityPoints.map((point) => (
              <li key={point} className="flex gap-2 text-sm text-muted">
                <span aria-hidden className="text-ok">
                  ✓
                </span>
                {point}
              </li>
            ))}
          </ul>
          <Link
            href={localePath(typed, "security")}
            className="btn-secondary mt-6 inline-flex"
          >
            {messages.nav.security}
          </Link>
        </div>
      </section>

      {faq.length > 0 && (
        <section className="container-page py-12" aria-labelledby="faq-heading">
          <h2 id="faq-heading" className="text-2xl font-semibold">
            {messages.home.faqHeading}
          </h2>
          <div className="mt-6 grid gap-3">
            {faq.map((item) => (
              <details key={item.q} className="card p-4">
                <summary className="cursor-pointer font-medium">
                  {item.q}
                </summary>
                <p className="mt-2 text-sm text-muted">{item.a}</p>
              </details>
            ))}
          </div>
        </section>
      )}

      <section className="container-page pb-20">
        <div className="card p-6 text-center sm:p-10">
          <h2 className="text-2xl font-semibold">
            {messages.home.secondaryCta}
          </h2>
          <div className="mx-auto mt-6 max-w-2xl">
            <WorkArea
              locale={typed}
              messages={messages}
              config={config}
              toolSlug="image-translator"
            />
          </div>
        </div>
      </section>
    </>
  );
}
