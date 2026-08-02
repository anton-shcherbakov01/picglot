import type { Metadata } from 'next';
import { notFound } from 'next/navigation';

import { ContactForm } from '@/components/ContactForm';
import { absoluteUrl, isLocale, localePath, type Locale } from '@/lib/i18n';

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  return {
    title: locale === 'ru' ? 'Связаться с нами' : 'Contact us',
    alternates: { canonical: absoluteUrl(localePath(locale, 'contact')) },
  };
}

export default async function ContactPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  return (
    <div className="container-page max-w-2xl py-12">
      <ContactForm locale={locale as Locale} />
    </div>
  );
}
