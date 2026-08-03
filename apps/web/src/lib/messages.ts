import { de } from '@/messages/de';
import { en, type Messages } from '@/messages/en';
import { es } from '@/messages/es';
import { fr } from '@/messages/fr';
import { id } from '@/messages/id';
import { pl } from '@/messages/pl';
import { pt } from '@/messages/pt';
import { ru } from '@/messages/ru';
import { tr } from '@/messages/tr';
import { uk } from '@/messages/uk';
import { DEFAULT_LOCALE, type Locale } from './i18n';

/** All ten advertised locales are fully translated. */
const DICTIONARIES: Partial<Record<Locale, Messages>> = { en, ru, es, de, fr, pt, tr, id, pl, uk };

export const MISSING_TRANSLATIONS: Locale[] = [];

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
