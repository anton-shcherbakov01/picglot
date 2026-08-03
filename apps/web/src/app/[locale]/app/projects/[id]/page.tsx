import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ProjectView } from "@/components/ProjectView";
import { serverFetch, type AppConfig } from "@/lib/api";
import { isLocale, type Locale } from "@/lib/i18n";
import { getMessages } from "@/lib/messages";

export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default async function ProjectPage({
  params,
}: {
  params: Promise<{ locale: string; id: string }>;
}) {
  const { locale, id } = await params;
  if (!isLocale(locale)) notFound();
  const typed = locale as Locale;

  // The project itself is fetched client-side: it is private, per-user data and
  // must never be cached by the SSR layer.
  const config = await serverFetch<AppConfig>("/api/v1/config", {
    revalidate: 600,
  });
  if (!config) notFound();

  return (
    <div className="container-page py-8">
      <ProjectView
        projectId={id}
        locale={typed}
        messages={getMessages(typed)}
        config={config}
      />
    </div>
  );
}
