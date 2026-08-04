import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

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
import { toolTitle } from "@/lib/tools";

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
      <section className="glow relative isolate overflow-hidden">
        <div aria-hidden className="dotfield absolute inset-x-0 top-0 h-72" />
        <div className="container-page relative pb-10 pt-12 sm:pt-20">
          <div className="mx-auto max-w-3xl text-center">
            <p className="eyebrow">PicGlot</p>
            <h1 className="mt-3 text-display">{messages.home.h1}</h1>
            <p className="mx-auto mt-5 max-w-2xl text-[17px] leading-relaxed text-muted">
              {messages.home.subtitle}
            </p>

            {/* One strip rather than three identical pills: the facts read as a
                single statement, and it collapses to rows on a phone. */}
            <ul className="mx-auto mt-7 inline-flex max-w-full flex-col divide-y divide-border rounded-panel border border-border bg-surface/70 text-left text-[13px] text-muted shadow-soft backdrop-blur sm:flex-row sm:divide-x sm:divide-y-0">
              {[
                messages.home.trustNoSignup,
                messages.home.trustDeleted,
                messages.home.trustFormats,
              ].map((fact) => (
                <li
                  key={fact}
                  className="flex items-center gap-2.5 px-4 py-2.5 sm:px-5"
                >
                  <span
                    aria-hidden
                    className="h-1.5 w-1.5 shrink-0 rounded-full bg-mint"
                  />
                  {fact}
                </li>
              ))}
            </ul>
          </div>

          <div className="mx-auto mt-10 max-w-3xl">
            <WorkArea
              locale={typed}
              messages={messages}
              config={config}
              toolSlug="image-translator"
            />
          </div>
        </div>
      </section>

      <section
        className="container-page py-16 sm:py-20"
        aria-labelledby="tools-heading"
      >
        <div className="max-w-2xl">
          <p className="eyebrow">01</p>
          <h2 id="tools-heading" className="mt-2 text-title">
            {messages.home.toolsHeading}
          </h2>
          <p className="mt-2 text-muted">{messages.home.toolsSubtitle}</p>
        </div>
        <ul className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {config.tools.map((tool, index) => (
            <li key={tool.slug}>
              <Link
                href={localePath(
                  typed,
                  toolSlugFor(typed, tool.slug, tool.localized_slugs),
                )}
                className="card-interactive group flex h-full flex-col p-5"
              >
                <span className="meta">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <span className="mt-2 font-semibold tracking-tight">
                  {toolTitle(tool.slug, typed)}
                </span>
                <span className="mt-auto pt-4 text-[13px] text-muted">
                  <span className="font-mono text-[11px] uppercase tracking-[0.06em]">
                    {tool.accepts.slice(0, 4).join(" ")}
                  </span>
                  <span
                    aria-hidden
                    className="mx-2 text-accent transition-transform duration-200 group-hover:translate-x-0.5 inline-block"
                  >
                    →
                  </span>
                  <span className="font-mono text-[11px] uppercase tracking-[0.06em]">
                    {tool.exports.slice(0, 3).join(" ")}
                  </span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </section>

      <section
        className="container-page py-16 sm:py-20"
        aria-labelledby="how-heading"
      >
        <div className="max-w-2xl">
          <p className="eyebrow">02</p>
          <h2 id="how-heading" className="mt-2 text-title">
            {messages.home.howHeading}
          </h2>
        </div>
        {/* The rule behind the steps reads as one continuous process rather
            than four unrelated cards. */}
        <ol className="relative mt-10 grid gap-8 sm:grid-cols-2 lg:grid-cols-4 lg:gap-6">
          <div
            aria-hidden
            className="rule-fade absolute inset-x-0 top-[15px] hidden lg:block"
          />
          {messages.home.howSteps.map((step, index) => (
            <li key={step.title} className="relative">
              <span
                aria-hidden
                className="grid h-8 w-8 place-items-center rounded-full border border-border bg-surface text-xs font-bold text-accent shadow-soft"
              >
                {index + 1}
              </span>
              <h3 className="mt-4 font-semibold tracking-tight">
                {step.title}
              </h3>
              <p className="mt-1.5 text-sm leading-relaxed text-muted">
                {step.body}
              </p>
            </li>
          ))}
        </ol>
      </section>

      <section
        className="container-page py-16 sm:py-20"
        aria-labelledby="security-heading"
      >
        <div className="card overflow-hidden">
          <div className="grid gap-8 p-6 sm:p-10 lg:grid-cols-[22rem_1fr]">
            <div>
              <p className="eyebrow">03</p>
              <h2 id="security-heading" className="mt-2 text-title">
                {messages.home.securityHeading}
              </h2>
              <Link
                href={localePath(typed, "security")}
                className="btn-secondary mt-6 inline-flex"
              >
                {messages.nav.security}
              </Link>
            </div>
            <ul className="grid content-start gap-px overflow-hidden rounded-card bg-border sm:grid-cols-2">
              {messages.home.securityPoints.map((point) => (
                <li
                  key={point}
                  className="flex gap-3 bg-surface p-4 text-sm leading-relaxed text-muted"
                >
                  <svg
                    aria-hidden
                    width="16"
                    height="16"
                    viewBox="0 0 16 16"
                    fill="none"
                    className="mt-0.5 shrink-0 text-mint"
                  >
                    <path
                      d="M3 8.4 6.2 11.6 13 4.8"
                      stroke="currentColor"
                      strokeWidth="1.8"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                  {point}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      {faq.length > 0 && (
        <section
          className="container-page py-16 sm:py-20"
          aria-labelledby="faq-heading"
        >
          <div className="max-w-2xl">
            <p className="eyebrow">04</p>
            <h2 id="faq-heading" className="mt-2 text-title">
              {messages.home.faqHeading}
            </h2>
          </div>
          <div className="mt-8 grid divide-y divide-border overflow-hidden rounded-card border border-border bg-surface shadow-soft">
            {faq.map((item) => (
              <details key={item.q} className="group px-5 py-4">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-4 font-medium">
                  {item.q}
                  <span
                    aria-hidden
                    className="shrink-0 text-lg leading-none text-muted transition-transform duration-200 group-open:rotate-45"
                  >
                    +
                  </span>
                </summary>
                <p className="mt-3 max-w-prose text-sm leading-relaxed text-muted">
                  {item.a}
                </p>
              </details>
            ))}
          </div>
        </section>
      )}

      <section className="container-page pb-24">
        <div className="glow relative isolate overflow-hidden rounded-panel border border-border bg-surface p-6 shadow-lift sm:p-12">
          <h2 className="text-center text-title">
            {messages.home.secondaryCta}
          </h2>
          <div className="mx-auto mt-8 max-w-2xl">
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
