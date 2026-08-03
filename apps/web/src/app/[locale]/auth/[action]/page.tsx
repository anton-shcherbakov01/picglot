import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { AuthForm } from "@/components/AuthForm";
import { absoluteUrl, isLocale, localePath, type Locale } from "@/lib/i18n";
import { getMessages } from "@/lib/messages";

const ACTIONS = [
  "sign-in",
  "sign-up",
  "forgot",
  "reset",
  "verify",
  "magic",
  "two-factor",
] as const;
type Action = (typeof ACTIONS)[number];

export function generateStaticParams() {
  return ACTIONS.map((action) => ({ action }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string; action: string }>;
}): Promise<Metadata> {
  const { locale, action } = await params;
  if (!isLocale(locale)) return {};
  const messages = getMessages(locale);
  const title =
    action === "sign-up"
      ? messages.auth.signUpTitle
      : messages.auth.signInTitle;
  return {
    title,
    // Auth screens have no business in a search index.
    robots: { index: false, follow: false },
    alternates: {
      canonical: absoluteUrl(localePath(locale, `auth/${action}`)),
    },
  };
}

export default async function AuthPage({
  params,
}: {
  params: Promise<{ locale: string; action: string }>;
}) {
  const { locale, action } = await params;
  if (!isLocale(locale) || !ACTIONS.includes(action as Action)) notFound();

  const typed = locale as Locale;
  const messages = getMessages(typed);

  return (
    <div className="container-page grid min-h-[70vh] place-items-center py-12">
      <div className="w-full max-w-md">
        {/* AuthForm reads ?token= from the URL, so it needs a boundary to be
            statically prerenderable. */}
        <Suspense fallback={<div className="skeleton h-80 rounded-card" />}>
          <AuthForm
            locale={typed}
            messages={messages}
            action={action as Action}
          />
        </Suspense>
      </div>
    </div>
  );
}
