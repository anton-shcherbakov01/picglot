import type { Metadata } from 'next';
import { notFound } from 'next/navigation';

import { findContentPage } from '@/content/legal';
import { absoluteUrl, alternates, isLocale, localePath, type Locale } from '@/lib/i18n';

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const page = findContentPage('security');
  const title = locale === 'ru' ? page?.title.ru : page?.title.en;
  const { languages } = alternates('/security');
  return {
    title,
    description:
      locale === 'ru'
        ? 'Как LingoImage AI хранит, защищает и удаляет ваши файлы.'
        : 'How LingoImage AI stores, protects and deletes your files.',
    alternates: { canonical: absoluteUrl(localePath(locale, 'security')), languages },
  };
}

export default async function SecurityPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const page = findContentPage('security');
  if (!page) notFound();

  const typed = locale as Locale;
  const pick = <T,>(value: { en: T; ru: T }): T => (typed === 'ru' ? value.ru : value.en);

  return (
    <article className="container-page max-w-3xl py-12">
      <h1 className="text-3xl font-bold">{pick(page.title)}</h1>
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
