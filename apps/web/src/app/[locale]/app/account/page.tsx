import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { AccountPanel } from "@/components/account/AccountPanel";
import { isLocale, type Locale } from "@/lib/i18n";
import { getMessages } from "@/lib/messages";

/** Private: every response depends on the signed-in user. */
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default async function AccountPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const typed = locale as Locale;
  return <AccountPanel locale={typed} messages={getMessages(typed)} />;
}
