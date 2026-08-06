/**
 * Locale handling.
 *
 * The locale always comes from the URL — never from a cookie alone — so every
 * page has one canonical address that search engines can index.
 */

export const LOCALES = [
  "en",
  "ru",
  "es",
  "de",
  "fr",
  "pt",
  "tr",
  "id",
  "pl",
] as const;
export type Locale = (typeof LOCALES)[number];

export const DEFAULT_LOCALE: Locale = "en";

export const LOCALE_NAMES: Record<Locale, string> = {
  en: "English",
  ru: "Русский",
  es: "Español",
  de: "Deutsch",
  fr: "Français",
  pt: "Português",
  tr: "Türkçe",
  id: "Bahasa Indonesia",
  pl: "Polski",
};

/** BCP-47 tags for `hreflang`, which needs regions for some languages. */
export const HREFLANG: Record<Locale, string> = {
  en: "en",
  ru: "ru",
  es: "es",
  de: "de",
  fr: "fr",
  pt: "pt",
  tr: "tr",
  id: "id",
  pl: "pl",
};

export function isLocale(value: string | undefined): value is Locale {
  return !!value && (LOCALES as readonly string[]).includes(value);
}

export function resolveLocale(value: string | undefined): Locale {
  return isLocale(value) ? value : DEFAULT_LOCALE;
}

/** Pick the best locale from an `Accept-Language` header. */
export function negotiateLocale(header: string | null): Locale {
  if (!header) return DEFAULT_LOCALE;
  const ranked = header
    .split(",")
    .map((part) => {
      const [tag, q] = part.trim().split(";q=");
      return { tag: (tag ?? "").trim().toLowerCase(), q: q ? Number(q) : 1 };
    })
    .sort((a, b) => b.q - a.q);

  for (const { tag } of ranked) {
    if (isLocale(tag)) return tag;
    const base = tag.split("-")[0];
    if (isLocale(base)) return base;
  }
  return DEFAULT_LOCALE;
}

export function localePath(locale: Locale, path = ""): string {
  const clean = path.startsWith("/") ? path : `/${path}`;
  return `/${locale}${clean === "/" ? "" : clean}`;
}

export const SITE_URL = (
  process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000"
).replace(/\/$/, "");

export function absoluteUrl(path: string): string {
  return `${SITE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

/** Alternate-language links for a path that exists in every locale. */
export function alternates(path: string): {
  canonical: string;
  languages: Record<string, string>;
} {
  const languages: Record<string, string> = {};
  for (const locale of LOCALES) {
    languages[HREFLANG[locale]] = absoluteUrl(localePath(locale, path));
  }
  languages["x-default"] = absoluteUrl(localePath(DEFAULT_LOCALE, path));
  return { canonical: "", languages };
}

/**
 * Slug overrides let a locale publish a tool under a native-language path
 * (`/ru/perevod-po-foto` rather than `/ru/translate-photo`). The map is served
 * by the API so the backend stays the single source of truth.
 */
export type SlugOverrides = Record<string, string> | undefined;

export function toolSlugFor(
  locale: Locale,
  slug: string,
  overrides: SlugOverrides,
): string {
  return overrides?.[locale] ?? slug;
}

/**
 * Alternate-language links for a tool, following each locale's own slug so a
 * localised page never points hreflang at a path that redirects.
 */
export function toolAlternates(
  slug: string,
  overrides: SlugOverrides,
): { languages: Record<string, string> } {
  const languages: Record<string, string> = {};
  for (const locale of LOCALES) {
    languages[HREFLANG[locale]] = absoluteUrl(
      localePath(locale, toolSlugFor(locale, slug, overrides)),
    );
  }
  languages["x-default"] = absoluteUrl(
    localePath(DEFAULT_LOCALE, toolSlugFor(DEFAULT_LOCALE, slug, overrides)),
  );
  return { languages };
}
