import { NextResponse, type NextRequest } from "next/server";

import { DEFAULT_LOCALE, LOCALES, negotiateLocale } from "./lib/i18n";

const PUBLIC_FILE =
  /\.(?:png|jpg|jpeg|svg|ico|webp|txt|xml|json|webmanifest|js|css|woff2?)$/;

/**
 * Locale routing.
 *
 * Every page lives under `/{locale}/…`. A request without a locale is
 * redirected once (308) to the negotiated locale — never rewritten — so each
 * page keeps exactly one canonical URL and no redirect chain can form.
 */
export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (
    pathname.startsWith("/_next") ||
    pathname.startsWith("/api/") ||
    pathname.startsWith("/share/") ||
    pathname === "/robots.txt" ||
    pathname === "/sitemap.xml" ||
    pathname.startsWith("/sitemaps/") ||
    pathname === "/manifest.webmanifest" ||
    PUBLIC_FILE.test(pathname)
  ) {
    return NextResponse.next();
  }

  const hasLocale = LOCALES.some(
    (locale) => pathname === `/${locale}` || pathname.startsWith(`/${locale}/`),
  );
  if (hasLocale) return NextResponse.next();

  const cookieLocale = request.cookies.get("locale")?.value;
  const locale =
    cookieLocale && (LOCALES as readonly string[]).includes(cookieLocale)
      ? cookieLocale
      : negotiateLocale(request.headers.get("accept-language"));

  const url = request.nextUrl.clone();
  url.pathname = `/${locale || DEFAULT_LOCALE}${pathname === "/" ? "" : pathname}`;
  return NextResponse.redirect(url, 308);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image).*)"],
};
