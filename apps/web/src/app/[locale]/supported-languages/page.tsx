import type { Metadata } from 'next';
import { notFound } from 'next/navigation';

import { serverFetch, type AppConfig } from '@/lib/api';
import { absoluteUrl, alternates, isLocale, localePath } from '@/lib/i18n';

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const { languages } = alternates('/supported-languages');
  return {
    title: locale === 'ru' ? 'Поддерживаемые языки' : 'Supported languages',
    description:
      locale === 'ru'
        ? 'Языки распознавания и перевода, поддержка письма справа налево и вертикального текста.'
        : 'Languages available for recognition and translation, with right-to-left and vertical script support.',
    alternates: {
      canonical: absoluteUrl(localePath(locale, 'supported-languages')),
      languages,
    },
  };
}

export default async function LanguagesPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const config = await serverFetch<AppConfig>('/api/v1/config', { revalidate: 3600 });
  if (!config) notFound();

  const ru = locale === 'ru';
  const yes = <span className="text-ok">✓</span>;
  const no = <span className="text-muted">—</span>;

  return (
    <div className="container-page py-12">
      <h1 className="text-3xl font-bold">
        {ru ? 'Поддерживаемые языки' : 'Supported languages'}
      </h1>
      <p className="mt-3 max-w-2xl text-muted">
        {ru
          ? 'Распознавание работает для языков со знаком в колонке OCR. Перевод требует настроенного поставщика; колонка «офлайн» показывает языки, для которых доступны локальные модели.'
          : 'Recognition works for every language marked in the OCR column. Translation needs a configured provider; the offline column shows which languages have local models available.'}
      </p>

      <div className="card mt-8 overflow-hidden">
        <div className="scroll-x">
          <table className="w-full text-sm">
            <caption className="sr-only">
              {ru ? 'Поддержка языков' : 'Language support'}
            </caption>
            <thead className="bg-raised text-left">
              <tr>
                <th scope="col" className="px-4 py-3 font-medium">
                  {ru ? 'Язык' : 'Language'}
                </th>
                <th scope="col" className="px-4 py-3 font-medium">
                  {ru ? 'Письменность' : 'Script'}
                </th>
                <th scope="col" className="px-4 py-3 text-center font-medium">
                  OCR
                </th>
                <th scope="col" className="px-4 py-3 text-center font-medium">
                  {ru ? 'Перевод' : 'Translation'}
                </th>
                <th scope="col" className="px-4 py-3 text-center font-medium">
                  {ru ? 'Офлайн' : 'Offline'}
                </th>
                <th scope="col" className="px-4 py-3 text-center font-medium">
                  {ru ? 'Особенности' : 'Notes'}
                </th>
              </tr>
            </thead>
            <tbody>
              {config.languages.map((language) => (
                <tr key={language.code} className="border-t border-border">
                  <th scope="row" className="px-4 py-3 text-left font-normal">
                    {language.name_native}
                    <span className="ml-2 text-xs text-muted">{language.name_en}</span>
                  </th>
                  <td className="px-4 py-3 text-muted">{language.script.replace('_', ' ')}</td>
                  <td className="px-4 py-3 text-center">{language.ocr ? yes : no}</td>
                  <td className="px-4 py-3 text-center">{language.translation ? yes : no}</td>
                  <td className="px-4 py-3 text-center">
                    {(language as { offline_translation?: boolean }).offline_translation ? yes : no}
                  </td>
                  <td className="px-4 py-3 text-center text-xs text-muted">
                    {language.rtl ? 'RTL' : language.cjk ? 'CJK' : ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <p className="mt-6 text-sm text-muted">
        {ru
          ? 'Языки с письмом справа налево наносятся с правильным порядком и соединением символов. Для японского и китайского поддерживается вертикальное написание.'
          : 'Right-to-left languages are drawn with correct ordering and joining. Japanese and Chinese also support vertical writing.'}
      </p>
    </div>
  );
}
