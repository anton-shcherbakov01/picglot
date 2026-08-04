"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { apiFetch, type AppConfig } from "@/lib/api";

import {
  LOCALES,
  LOCALE_NAMES,
  localePath,
  toolSlugFor,
  type Locale,
} from "@/lib/i18n";
import type { Messages } from "@/lib/messages";
import { toolTitle } from "@/lib/tools";

import { Wordmark } from "./Brand";

interface Props {
  locale: Locale;
  messages: Messages;
  tools: AppConfig["tools"];
}

interface SessionUser {
  name: string | null;
  email: string;
  admin_role: string | null;
}

/**
 * Site header.
 *
 * The phone layout is the constraint that shapes it: at 360 CSS pixels only the
 * mark and two controls fit, so everything else lives in a sheet rather than
 * being hidden with `sm:` and quietly lost. The sheet scrolls on its own —
 * it hangs off a sticky header, so the page behind it cannot bring the lower
 * items into view.
 */
export function Header({ locale, messages, tools }: Props) {
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [user, setUser] = useState<SessionUser | null>(null);
  const toolsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Quietly probe the session; anonymous visitors simply get no user.
    apiFetch<SessionUser>("/api/v1/auth/me")
      .then(setUser)
      .catch(() => setUser(null));
  }, []);

  useEffect(() => {
    setMenuOpen(false);
    setToolsOpen(false);
  }, [pathname]);

  // While the sheet is open the page beneath it must not scroll, or a swipe
  // that misses the sheet silently moves the wrong thing.
  useEffect(() => {
    if (!menuOpen) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [menuOpen]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setMenuOpen(false);
      setToolsOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!toolsOpen) return;
    const onPointer = (event: PointerEvent) => {
      if (!toolsRef.current?.contains(event.target as Node))
        setToolsOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, [toolsOpen]);

  /** Same page, different language — keeps the visitor where they were. */
  const switchLocale = (next: Locale) => {
    const rest = pathname.replace(/^\/[a-z]{2}(?=\/|$)/, "") || "/";
    return localePath(next, rest);
  };

  const toolHref = (tool: AppConfig["tools"][number]) =>
    localePath(locale, toolSlugFor(locale, tool.slug, tool.localized_slugs));

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-bg/80 backdrop-blur-xl">
      <div className="container-page flex h-14 items-center gap-3 sm:h-16">
        <Link
          href={localePath(locale)}
          className="flex shrink-0 items-center gap-2.5 font-semibold tracking-tight"
        >
          <Wordmark />
          <span className="text-[15px]">PicGlot</span>
        </Link>

        <nav
          aria-label="Main"
          className="hidden flex-1 items-center gap-0.5 md:flex"
        >
          <div className="relative" ref={toolsRef}>
            <button
              type="button"
              className="btn-ghost px-3"
              aria-expanded={toolsOpen}
              aria-haspopup="true"
              onClick={() => setToolsOpen((open) => !open)}
            >
              {messages.nav.tools}
              <Chevron open={toolsOpen} />
            </button>
            {toolsOpen && (
              <div
                className="absolute left-0 top-full mt-2 grid w-[36rem] grid-cols-2 gap-0.5 rounded-panel border border-border bg-surface p-2 shadow-lift animate-sheet-in"
                role="menu"
              >
                {tools.map((tool) => (
                  <Link
                    key={tool.slug}
                    role="menuitem"
                    href={toolHref(tool)}
                    className="group rounded-xl px-3 py-2.5 transition-colors hover:bg-raised"
                  >
                    <span className="block text-sm font-medium">
                      {toolTitle(tool.slug, locale)}
                    </span>
                    <span className="meta mt-0.5 block">
                      {tool.accepts
                        .slice(0, 4)
                        .map((ext) => ext)
                        .join(" · ")}
                    </span>
                  </Link>
                ))}
              </div>
            )}
          </div>
          <Link href={localePath(locale, "pricing")} className="btn-ghost px-3">
            {messages.nav.pricing}
          </Link>
          <Link href={localePath(locale, "api")} className="btn-ghost px-3">
            {messages.nav.api}
          </Link>
          <Link
            href={localePath(locale, "supported-languages")}
            className="btn-ghost px-3"
          >
            {messages.nav.languages}
          </Link>
        </nav>

        <div className="ml-auto flex items-center gap-1">
          <div className="hidden md:block">
            <LocaleSwitcher locale={locale} hrefFor={switchLocale} />
          </div>
          <ThemeToggle label={messages.common.theme} />

          {/* Desktop account actions. On a phone these live in the sheet, where
              there is room to label them. */}
          <div className="hidden items-center gap-1 md:flex">
            {user ? (
              <>
                {user.admin_role && (
                  <Link
                    href={localePath(locale, "admin")}
                    className="btn-ghost px-3"
                  >
                    Admin
                  </Link>
                )}
                <Link
                  href={localePath(locale, "app")}
                  className="btn-secondary"
                >
                  {messages.nav.dashboard}
                </Link>
              </>
            ) : (
              <>
                <Link
                  href={localePath(locale, "auth/sign-in")}
                  className="btn-ghost px-3"
                >
                  {messages.nav.signIn}
                </Link>
                <Link
                  href={localePath(locale, "auth/sign-up")}
                  className="btn-primary"
                >
                  {messages.nav.signUp}
                </Link>
              </>
            )}
          </div>

          {/* One visible action on a phone, and only when there is room for it:
              below 380px the mark plus three controls already fill the bar. */}
          {!user && (
            <Link
              href={localePath(locale, "auth/sign-up")}
              className="btn-primary hidden px-3 py-2 text-[13px] min-[380px]:inline-flex md:hidden"
            >
              {messages.nav.signUp}
            </Link>
          )}

          <button
            type="button"
            className="btn-ghost px-2.5 md:hidden"
            aria-expanded={menuOpen}
            aria-controls="mobile-nav"
            onClick={() => setMenuOpen((open) => !open)}
          >
            <span className="sr-only">Menu</span>
            {menuOpen ? <CloseIcon /> : <MenuIcon />}
          </button>
        </div>
      </div>

      {menuOpen && (
        <nav
          id="mobile-nav"
          aria-label="Mobile"
          // Height is bounded by the viewport minus the bar above it, so the
          // list scrolls inside the sheet instead of running off the screen.
          className="safe-b max-h-[calc(100dvh-3.5rem)] overflow-y-auto overscroll-contain border-t border-border bg-surface px-4 pt-4 animate-sheet-in md:hidden"
        >
          <div className="grid gap-2">
            {user ? (
              <>
                <Link
                  href={localePath(locale, "app")}
                  className="btn-primary w-full"
                >
                  {messages.nav.dashboard}
                </Link>
                <div className="grid grid-cols-2 gap-2">
                  <Link
                    href={localePath(locale, "app/account")}
                    className="btn-secondary"
                  >
                    {messages.nav.account}
                  </Link>
                  {user.admin_role ? (
                    <Link
                      href={localePath(locale, "admin")}
                      className="btn-secondary"
                    >
                      Admin
                    </Link>
                  ) : (
                    <Link
                      href={localePath(locale, "pricing")}
                      className="btn-secondary"
                    >
                      {messages.nav.pricing}
                    </Link>
                  )}
                </div>
              </>
            ) : (
              <div className="grid grid-cols-2 gap-2">
                <Link
                  href={localePath(locale, "auth/sign-in")}
                  className="btn-secondary"
                >
                  {messages.nav.signIn}
                </Link>
                <Link
                  href={localePath(locale, "auth/sign-up")}
                  className="btn-primary"
                >
                  {messages.nav.signUp}
                </Link>
              </div>
            )}
          </div>

          <p className="eyebrow mb-2 mt-6">{messages.nav.tools}</p>
          <div className="grid gap-0.5">
            {tools.map((tool) => (
              <Link
                key={tool.slug}
                href={toolHref(tool)}
                className="rounded-xl px-3 py-2.5 text-sm transition-colors hover:bg-raised"
              >
                {toolTitle(tool.slug, locale)}
              </Link>
            ))}
          </div>

          <div className="rule-fade my-4" />

          <div className="grid gap-0.5">
            {[
              ["pricing", messages.nav.pricing],
              ["api", messages.nav.api],
              ["supported-languages", messages.nav.languages],
              ["blog", messages.nav.blog],
              ["security", messages.nav.security],
              ["contact", messages.nav.contact],
            ].map(([path, label]) => (
              <Link
                key={path}
                href={localePath(locale, path as string)}
                className="rounded-xl px-3 py-2.5 text-sm text-muted transition-colors hover:bg-raised hover:text-fg"
              >
                {label}
              </Link>
            ))}
          </div>

          <div className="rule-fade my-4" />

          <p className="eyebrow mb-2">{LOCALE_NAMES[locale]}</p>
          <div className="grid grid-cols-2 gap-0.5 pb-2">
            {LOCALES.map((item) => (
              <Link
                key={item}
                href={switchLocale(item)}
                hrefLang={item}
                className={`rounded-xl px-3 py-2 text-sm transition-colors hover:bg-raised ${
                  item === locale ? "font-semibold text-accent" : "text-muted"
                }`}
              >
                {LOCALE_NAMES[item]}
              </Link>
            ))}
          </div>
        </nav>
      )}
    </header>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      aria-hidden
      width="12"
      height="12"
      viewBox="0 0 12 12"
      fill="none"
      className={`transition-transform duration-200 ${open ? "rotate-180" : ""}`}
    >
      <path
        d="M2.5 4.5 6 8l3.5-3.5"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function MenuIcon() {
  return (
    <svg aria-hidden width="20" height="20" viewBox="0 0 20 20" fill="none">
      <path
        d="M3 5.5h14M3 10h14M3 14.5h14"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg aria-hidden width="20" height="20" viewBox="0 0 20 20" fill="none">
      <path
        d="M5 5l10 10M15 5L5 15"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
    </svg>
  );
}

function LocaleSwitcher({
  locale,
  hrefFor,
}: {
  locale: Locale;
  hrefFor: (next: Locale) => string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        className="btn-ghost gap-1.5 px-2.5"
        aria-expanded={open}
        aria-label="Change language"
        onClick={() => setOpen((value) => !value)}
      >
        <svg aria-hidden width="17" height="17" viewBox="0 0 20 20" fill="none">
          <circle
            cx="10"
            cy="10"
            r="7.2"
            stroke="currentColor"
            strokeWidth="1.5"
          />
          <path
            d="M2.8 10h14.4M10 2.8c1.9 2 2.9 4.5 2.9 7.2s-1 5.2-2.9 7.2c-1.9-2-2.9-4.5-2.9-7.2S8.1 4.8 10 2.8Z"
            stroke="currentColor"
            strokeWidth="1.5"
          />
        </svg>
        <span className="text-xs font-semibold uppercase">{locale}</span>
      </button>
      {open && (
        <ul className="absolute right-0 top-full z-50 mt-2 max-h-80 w-48 overflow-y-auto rounded-panel border border-border bg-surface p-1 shadow-lift animate-sheet-in">
          {LOCALES.map((item) => (
            <li key={item}>
              <Link
                href={hrefFor(item)}
                hrefLang={item}
                className={`block rounded-lg px-3 py-2 text-sm hover:bg-raised ${
                  item === locale ? "font-semibold text-accent" : ""
                }`}
              >
                {LOCALE_NAMES[item]}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ThemeToggle({ label }: { label: string }) {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
  }, []);

  const toggle = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("theme", next ? "dark" : "light");
    } catch {
      /* private mode */
    }
  };

  return (
    <button
      type="button"
      onClick={toggle}
      className="btn-ghost px-2.5"
      aria-label={label}
      aria-pressed={dark}
    >
      {dark ? (
        <svg aria-hidden width="17" height="17" viewBox="0 0 20 20" fill="none">
          <circle
            cx="10"
            cy="10"
            r="3.6"
            stroke="currentColor"
            strokeWidth="1.6"
          />
          <path
            d="M10 1.8v2.1M10 16.1v2.1M18.2 10h-2.1M3.9 10H1.8M15.8 4.2l-1.5 1.5M5.7 14.3l-1.5 1.5M15.8 15.8l-1.5-1.5M5.7 5.7 4.2 4.2"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
          />
        </svg>
      ) : (
        <svg aria-hidden width="17" height="17" viewBox="0 0 20 20" fill="none">
          <path
            d="M16.5 12.4A7 7 0 0 1 7.6 3.5a7 7 0 1 0 8.9 8.9Z"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinejoin="round"
          />
        </svg>
      )}
    </button>
  );
}
