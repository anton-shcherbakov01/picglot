import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { AdminPanel } from "@/components/admin/AdminPanel";
import { isLocale, type Locale } from "@/lib/i18n";

export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default async function AdminPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  return <AdminPanel locale={locale as Locale} />;
}
