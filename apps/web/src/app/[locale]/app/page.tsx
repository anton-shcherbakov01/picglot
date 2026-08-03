import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { Dashboard } from "@/components/Dashboard";
import { isLocale, type Locale } from "@/lib/i18n";
import { getMessages } from "@/lib/messages";

export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default async function DashboardPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const typed = locale as Locale;
  return <Dashboard locale={typed} messages={getMessages(typed)} />;
}
