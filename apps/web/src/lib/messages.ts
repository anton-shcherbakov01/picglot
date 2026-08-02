import { en, type Messages } from '@/messages/en';
import { ru } from '@/messages/ru';
import { DEFAULT_LOCALE, type Locale } from './i18n';

/**
 * Locale dictionaries.
 *
 * `en` and `ru` are complete. The other advertised locales currently reuse the
 * English strings for body copy while their URLs, metadata and language names
 * are localised — a partially translated interface is better than a missing
 * page, and `MISSING_TRANSLATIONS` records the truth rather than hiding it.
 */
const DICTIONARIES: Partial<Record<Locale, Messages>> = { en, ru };

export const MISSING_TRANSLATIONS: Locale[] = [
  'es',
  'de',
  'fr',
  'pt',
  'tr',
  'id',
  'pl',
  'uk',
];

export function getMessages(locale: Locale): Messages {
  return DICTIONARIES[locale] ?? DICTIONARIES[DEFAULT_LOCALE] ?? en;
}

export function isFullyTranslated(locale: Locale): boolean {
  return !MISSING_TRANSLATIONS.includes(locale);
}

/** `format('Up to {size}', { size: '10 MB' })` → `'Up to 10 MB'` */
export function format(template: string, values: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (match, key: string) =>
    key in values ? String(values[key]) : match,
  );
}

export type { Messages };
