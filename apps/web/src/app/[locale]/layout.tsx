import type { Metadata } from 'next';
import { notFound } from 'next/navigation';

import { Footer } from '@/components/Footer';
import { Header } from '@/components/Header';
import { serverFetch, type AppConfig } from '@/lib/api';
import { LOCALES, absoluteUrl, isLocale, localePath, type Locale } from '@/lib/i18n';
import { getMessages } from '@/lib/messages';

export function generateStaticParams() {
  return LOCALES.map((locale) => ({ locale }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  return {
    metadataBase: new URL(absoluteUrl('/')),
    openGraph: { locale, siteName: 'LingoImage AI', type: 'website' },
    twitter: { card: 'summary_large_image' },
  };
}

/** Locales whose scripts run right to left. */
const RTL_LOCALES = new Set<string>([]);

export default async function LocaleLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const typed = locale as Locale;
  const messages = getMessages(typed);
  const config = await serverFetch<AppConfig>('/api/v1/config', { revalidate: 600 });
  const tools = config?.tools ?? [];

  return (
    <div lang={typed} dir={RTL_LOCALES.has(typed) ? 'rtl' : 'ltr'} className="flex min-h-dvh flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-accent focus:px-4 focus:py-2 focus:text-accent-fg"
      >
        {messages.common.skipToContent}
      </a>

      {config?.maintenance_mode && (
        <div role="status" className="bg-warn/15 px-4 py-2 text-center text-sm text-warn">
          {messages.errors.maintenance}
        </div>
      )}

      <Header locale={typed} messages={messages} tools={tools} />
      <main id="main" className="flex-1">
        {children}
      </main>
      <Footer
        locale={typed}
        messages={messages}
        toolSlugs={tools.map((tool) => tool.slug)}
      />
    </div>
  );
}

export { localePath };
