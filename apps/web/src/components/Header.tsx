'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';

import { apiFetch, type AppConfig } from '@/lib/api';
import { LOCALES, LOCALE_NAMES, localePath, type Locale } from '@/lib/i18n';
import type { Messages } from '@/lib/messages';

interface Props {
  locale: Locale;
  messages: Messages;
  tools: AppConfig['tools'];
}

export function Header({ locale, messages, tools }: Props) {
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [user, setUser] = useState<{
    name: string | null;
    email: string;
    admin_role: string | null;
  } | null>(null);

  useEffect(() => {
    // Quietly probe the session; anonymous visitors simply get no user.
    apiFetch<{ name: string | null; email: string; admin_role: string | null }>('/api/v1/auth/me')
      .then(setUser)
      .catch(() => setUser(null));
  }, []);

  useEffect(() => {
    setMenuOpen(false);
    setToolsOpen(false);
  }, [pathname]);

  /** Same page, different language — keeps the visitor where they were. */
  const switchLocale = (next: Locale) => {
    const rest = pathname.replace(/^\/[a-z]{2}(?=\/|$)/, '') || '/';
    return localePath(next, rest);
  };

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-bg/85 backdrop-blur">
      <div className="container-page flex h-16 items-center gap-4">
        <Link
          href={localePath(locale)}
          className="flex shrink-0 items-center gap-2 font-semibold tracking-tight"
        >
          <span
            aria-hidden
            className="grid h-7 w-7 place-items-center rounded-md bg-accent text-[13px] font-bold text-accent-fg"
          >
            Li
          </span>
          <span className="hidden sm:inline">LingoImage AI</span>
        </Link>

        <nav aria-label="Main" className="hidden flex-1 items-center gap-1 md:flex">
          <div className="relative">
            <button
              type="button"
              className="btn-ghost"
              aria-expanded={toolsOpen}
              aria-haspopup="true"
              onClick={() => setToolsOpen((open) => !open)}
            >
              {messages.nav.tools}
              <span aria-hidden className="text-xs">
                ▾
              </span>
            </button>
            {toolsOpen && (
              <div
                className="absolute left-0 top-full mt-1 grid w-[34rem] grid-cols-2 gap-1 rounded-card border border-border bg-surface p-2 shadow-lg"
                role="menu"
              >
                {tools.map((tool) => (
                  <Link
                    key={tool.slug}
                    role="menuitem"
                    href={localePath(locale, tool.slug)}
                    className="rounded-lg px-3 py-2 text-sm hover:bg-raised"
                  >
                    {toolTitle(tool.slug, locale)}
                  </Link>
                ))}
              </div>
            )}
          </div>
          <Link href={localePath(locale, 'pricing')} className="btn-ghost">
            {messages.nav.pricing}
          </Link>
          <Link href={localePath(locale, 'api')} className="btn-ghost">
            {messages.nav.api}
          </Link>
          <Link href={localePath(locale, 'supported-languages')} className="btn-ghost">
            {messages.nav.languages}
          </Link>
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <LocaleSwitcher locale={locale} hrefFor={switchLocale} />
          <ThemeToggle label={messages.common.theme} />
          {user ? (
            <>
              {user.admin_role && (
                <Link
                  href={localePath(locale, 'admin')}
                  className="btn-ghost hidden sm:inline-flex"
                >
                  Admin
                </Link>
              )}
              <Link href={localePath(locale, 'app')} className="btn-secondary hidden sm:inline-flex">
                {messages.nav.dashboard}
              </Link>
            </>
          ) : (
            <>
              <Link
                href={localePath(locale, 'auth/sign-in')}
                className="btn-ghost hidden sm:inline-flex"
              >
                {messages.nav.signIn}
              </Link>
              <Link href={localePath(locale, 'auth/sign-up')} className="btn-primary">
                {messages.nav.signUp}
              </Link>
            </>
          )}
          <button
            type="button"
            className="btn-ghost md:hidden"
            aria-expanded={menuOpen}
            aria-controls="mobile-nav"
            onClick={() => setMenuOpen((open) => !open)}
          >
            <span className="sr-only">Menu</span>
            <span aria-hidden>{menuOpen ? '✕' : '☰'}</span>
          </button>
        </div>
      </div>

      {menuOpen && (
        <nav
          id="mobile-nav"
          aria-label="Mobile"
          className="border-t border-border bg-surface px-4 py-3 md:hidden"
        >
          <div className="grid gap-1">
            {tools.map((tool) => (
              <Link
                key={tool.slug}
                href={localePath(locale, tool.slug)}
                className="rounded-lg px-3 py-2 text-sm hover:bg-raised"
              >
                {toolTitle(tool.slug, locale)}
              </Link>
            ))}
            <hr className="my-2 border-border" />
            <Link href={localePath(locale, 'pricing')} className="rounded-lg px-3 py-2 text-sm">
              {messages.nav.pricing}
            </Link>
            <Link href={localePath(locale, 'api')} className="rounded-lg px-3 py-2 text-sm">
              {messages.nav.api}
            </Link>
            {user && (
              <Link href={localePath(locale, 'app')} className="rounded-lg px-3 py-2 text-sm">
                {messages.nav.dashboard}
              </Link>
            )}
          </div>
        </nav>
      )}
    </header>
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
  return (
    <div className="relative">
      <button
        type="button"
        className="btn-ghost px-2"
        aria-expanded={open}
        aria-label="Change language"
        onClick={() => setOpen((value) => !value)}
      >
        <span aria-hidden>🌐</span>
        <span className="hidden text-xs uppercase sm:inline">{locale}</span>
      </button>
      {open && (
        <ul className="absolute right-0 top-full z-50 mt-1 max-h-80 w-48 overflow-y-auto rounded-card border border-border bg-surface p-1 shadow-lg">
          {LOCALES.map((item) => (
            <li key={item}>
              <Link
                href={hrefFor(item)}
                hrefLang={item}
                className={`block rounded-lg px-3 py-2 text-sm hover:bg-raised ${
                  item === locale ? 'font-semibold text-accent' : ''
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
    setDark(document.documentElement.classList.contains('dark'));
  }, []);

  const toggle = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle('dark', next);
    try {
      localStorage.setItem('theme', next ? 'dark' : 'light');
    } catch {
      /* private mode */
    }
  };

  return (
    <button type="button" onClick={toggle} className="btn-ghost px-2" aria-label={label}>
      <span aria-hidden>{dark ? '☀' : '☾'}</span>
    </button>
  );
}

/** Tool names are short enough to keep inline rather than in every dictionary. */
const TOOL_TITLES: Record<string, { en: string; ru: string }> = {
  'image-translator': { en: 'Image translator', ru: 'Переводчик изображений' },
  'translate-photo': { en: 'Photo translator', ru: 'Переводчик фотографий' },
  'screenshot-translator': { en: 'Screenshot translator', ru: 'Переводчик скриншотов' },
  'image-to-text': { en: 'Image to text', ru: 'Изображение в текст' },
  'jpg-to-word': { en: 'Photo to Word', ru: 'Фото в Word' },
  'image-to-excel': { en: 'Image to Excel', ru: 'Изображение в Excel' },
  'handwriting-to-text': { en: 'Handwriting to text', ru: 'Рукописный текст' },
  'pdf-translator': { en: 'PDF translator', ru: 'Переводчик PDF' },
  'pdf-ocr': { en: 'PDF OCR', ru: 'OCR для PDF' },
  'document-scanner': { en: 'Document scanner', ru: 'Сканер документов' },
  'receipt-scanner': { en: 'Receipt scanner', ru: 'Сканер чеков' },
  'invoice-ocr': { en: 'Invoice OCR', ru: 'Распознавание счетов' },
  batch: { en: 'Batch processing', ru: 'Пакетная обработка' },
};

export function toolTitle(slug: string, locale: Locale): string {
  const entry = TOOL_TITLES[slug];
  if (!entry) return slug;
  return locale === 'ru' ? entry.ru : entry.en;
}
