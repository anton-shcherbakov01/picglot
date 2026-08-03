import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { CONTENT_PAGES, findContentPage } from "@/content/legal";
import {
  absoluteUrl,
  alternates,
  isLocale,
  localePath,
  type Locale,
} from "@/lib/i18n";

export function generateStaticParams() {
  return CONTENT_PAGES.filter((page) => page.slug.startsWith("legal/")).map(
    (page) => ({
      slug: page.slug.replace("legal/", ""),
    }),
  );
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string; slug: string }>;
}): Promise<Metadata> {
  const { locale, slug } = await params;
  const page = findContentPage(`legal/${slug}`);
  if (!isLocale(locale) || !page) return {};
  const title = locale === "ru" ? page.title.ru : page.title.en;
  const { languages } = alternates(`/legal/${slug}`);
  return {
    title,
    description: title,
    alternates: {
      canonical: absoluteUrl(localePath(locale, `legal/${slug}`)),
      languages,
    },
  };
}

export default async function LegalPage({
  params,
}: {
  params: Promise<{ locale: string; slug: string }>;
}) {
  const { locale, slug } = await params;
  if (!isLocale(locale)) notFound();
  const page = findContentPage(`legal/${slug}`);
  if (!page) notFound();

  const typed = locale as Locale;
  const pick = <T,>(value: { en: T; ru: T }): T =>
    typed === "ru" ? value.ru : value.en;

  return (
    <article className="container-page max-w-3xl py-12">
      <h1 className="text-3xl font-bold">{pick(page.title)}</h1>
      <p className="mt-2 text-sm text-muted">
        {typed === "ru" ? "Обновлено" : "Last updated"}: {page.updated}
      </p>

      {page.template && (
        <p className="mt-4 rounded-lg border border-warn/40 bg-warn/10 px-4 py-3 text-sm text-warn">
          {typed === "ru"
            ? "Это шаблон. Он описывает, как сервис работает на самом деле, но перед запуском в production требует проверки юристом вашей юрисдикции."
            : "This is a template. It describes how the service actually behaves, but it must be reviewed by a lawyer for your jurisdiction before production use."}
        </p>
      )}

      <div className="prose-content mt-8">
        {page.sections.map((section) => (
          <section key={section.heading.en}>
            <h2>{pick(section.heading)}</h2>
            <ul>
              {pick(section.body).map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </article>
  );
}
