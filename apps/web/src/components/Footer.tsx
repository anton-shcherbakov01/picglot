import Link from "next/link";

import type { AppConfig } from "@/lib/api";
import { localePath, toolSlugFor, type Locale } from "@/lib/i18n";
import type { Messages } from "@/lib/messages";
import { toolTitle } from "./Header";

export function Footer({
  locale,
  messages,
  tools,
}: {
  locale: Locale;
  messages: Messages;
  tools: AppConfig["tools"];
}) {
  const year = new Date().getFullYear();

  return (
    <footer className="mt-20 border-t border-border bg-surface">
      <div className="container-page grid gap-10 py-12 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <div className="mb-3 flex items-center gap-2 font-semibold">
            <span
              aria-hidden
              className="grid h-6 w-6 place-items-center rounded bg-accent text-[11px] font-bold text-accent-fg"
            >
              PG
            </span>
            PicGlot
          </div>
          <p className="text-sm text-muted">{messages.brand.tagline}</p>
        </div>

        <nav aria-labelledby="footer-product">
          <h2 id="footer-product" className="mb-3 text-sm font-semibold">
            {messages.footer.product}
          </h2>
          <ul className="space-y-2 text-sm text-muted">
            {tools.slice(0, 7).map((tool) => (
              <li key={tool.slug}>
                <Link
                  href={localePath(
                    locale,
                    toolSlugFor(locale, tool.slug, tool.localized_slugs),
                  )}
                  className="hover:text-fg"
                >
                  {toolTitle(tool.slug, locale)}
                </Link>
              </li>
            ))}
            <li>
              <Link
                href={localePath(locale, "pricing")}
                className="hover:text-fg"
              >
                {messages.nav.pricing}
              </Link>
            </li>
          </ul>
        </nav>

        <nav aria-labelledby="footer-company">
          <h2 id="footer-company" className="mb-3 text-sm font-semibold">
            {messages.footer.company}
          </h2>
          <ul className="space-y-2 text-sm text-muted">
            <li>
              <Link href={localePath(locale, "api")} className="hover:text-fg">
                {messages.nav.api}
              </Link>
            </li>
            <li>
              <Link href={localePath(locale, "blog")} className="hover:text-fg">
                {messages.nav.blog}
              </Link>
            </li>
            <li>
              <Link
                href={localePath(locale, "supported-languages")}
                className="hover:text-fg"
              >
                {messages.nav.languages}
              </Link>
            </li>
            <li>
              <Link
                href={localePath(locale, "status")}
                className="hover:text-fg"
              >
                {messages.nav.status}
              </Link>
            </li>
            <li>
              <Link
                href={localePath(locale, "contact")}
                className="hover:text-fg"
              >
                {messages.nav.contact}
              </Link>
            </li>
          </ul>
        </nav>

        <nav aria-labelledby="footer-legal">
          <h2 id="footer-legal" className="mb-3 text-sm font-semibold">
            {messages.footer.legal}
          </h2>
          <ul className="space-y-2 text-sm text-muted">
            <li>
              <Link
                href={localePath(locale, "legal/privacy")}
                className="hover:text-fg"
              >
                {messages.footer.privacy}
              </Link>
            </li>
            <li>
              <Link
                href={localePath(locale, "legal/terms")}
                className="hover:text-fg"
              >
                {messages.footer.terms}
              </Link>
            </li>
            <li>
              <Link
                href={localePath(locale, "legal/cookies")}
                className="hover:text-fg"
              >
                {messages.footer.cookies}
              </Link>
            </li>
            <li>
              <Link
                href={localePath(locale, "legal/refunds")}
                className="hover:text-fg"
              >
                {messages.footer.refunds}
              </Link>
            </li>
            <li>
              <Link
                href={localePath(locale, "legal/acceptable-use")}
                className="hover:text-fg"
              >
                {messages.footer.acceptableUse}
              </Link>
            </li>
            <li>
              <Link
                href={localePath(locale, "security")}
                className="hover:text-fg"
              >
                {messages.footer.security}
              </Link>
            </li>
          </ul>
        </nav>
      </div>

      <div className="border-t border-border">
        <div className="container-page py-5 text-xs text-muted">
          © {year} PicGlot. {messages.footer.rights}
        </div>
      </div>
    </footer>
  );
}
