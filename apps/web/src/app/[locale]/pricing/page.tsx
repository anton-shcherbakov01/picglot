import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { serverFetch, type AppConfig } from "@/lib/api";
import { formatMoney, formatNumber } from "@/lib/format";
import {
  absoluteUrl,
  alternates,
  isLocale,
  localePath,
  type Locale,
} from "@/lib/i18n";
import { getMessages } from "@/lib/messages";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const messages = getMessages(locale);
  const { languages } = alternates("/pricing");
  return {
    title: messages.pricing.title,
    description: messages.pricing.subtitle,
    alternates: {
      canonical: absoluteUrl(localePath(locale, "pricing")),
      languages,
    },
  };
}

const FEATURE_LABELS: Record<string, { en: string; ru: string }> = {
  projects: { en: "Saved projects and history", ru: "Проекты и история" },
  batch: { en: "Batch processing", ru: "Пакетная обработка" },
  docx_export: { en: "Word (.docx) export", ru: "Экспорт в Word (.docx)" },
  xlsx_export: { en: "Excel (.xlsx) export", ru: "Экспорт в Excel (.xlsx)" },
  searchable_pdf: { en: "Searchable PDF", ru: "Searchable PDF" },
  priority_queue: { en: "Priority queue", ru: "Приоритетная очередь" },
  advanced_ocr: { en: "Advanced recognition", ru: "Расширенное распознавание" },
  advanced_inpaint: {
    en: "Advanced background repair",
    ru: "Улучшенное восстановление фона",
  },
  glossary: { en: "Glossaries", ru: "Глоссарии" },
  translation_memory: { en: "Translation memory", ru: "Память переводов" },
  email_notifications: {
    en: "Email notifications",
    ru: "Уведомления по email",
  },
  no_branding: { en: "No branding on results", ru: "Без брендирования" },
  api_limited: { en: "API access", ru: "Доступ к API" },
  api_full: { en: "Full API access", ru: "Полный доступ к API" },
  webhooks: { en: "Webhooks", ru: "Webhooks" },
  workspace: { en: "Team workspace", ru: "Рабочее пространство команды" },
  audit_log: { en: "Audit log", ru: "Журнал аудита" },
  central_billing: {
    en: "Centralised billing",
    ru: "Централизованный биллинг",
  },
  priority_support: { en: "Priority support", ru: "Приоритетная поддержка" },
  custom_retention: {
    en: "Custom retention period",
    ru: "Настраиваемый срок хранения",
  },
  local_only_processing: {
    en: "Local-only processing mode",
    ru: "Режим локальной обработки",
  },
  basic_ocr: { en: "Text recognition", ru: "Распознавание текста" },
  basic_translation: { en: "Translation", ru: "Перевод" },
  history: { en: "History", ru: "История" },
};

export default async function PricingPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const typed = locale as Locale;
  const messages = getMessages(typed);

  const config = await serverFetch<AppConfig>("/api/v1/config", {
    revalidate: 600,
  });
  if (!config) notFound();

  const useRub = typed === "ru";
  const currency = useRub ? "RUB" : "USD";
  const priceOf = (plan: AppConfig["plans"][number]) =>
    useRub ? plan.price_rub_kopecks : plan.price_usd_cents;

  return (
    <div className="container-page py-12 sm:py-16">
      <div className="mx-auto max-w-2xl text-center">
        <h1 className="text-3xl font-bold sm:text-4xl">
          {messages.pricing.title}
        </h1>
        <p className="mt-3 text-muted">{messages.pricing.subtitle}</p>
      </div>

      <div className="mt-10 grid gap-4 lg:grid-cols-3">
        {config.plans.map((plan) => {
          const highlight = plan.code === "pro";
          return (
            <section
              key={plan.code}
              aria-labelledby={`plan-${plan.code}`}
              className={`card flex flex-col p-6 ${highlight ? "ring-2 ring-accent" : ""}`}
            >
              <h2 id={`plan-${plan.code}`} className="text-lg font-semibold">
                {plan.name}
              </h2>
              <p className="mt-3 text-3xl font-bold">
                {priceOf(plan) === 0
                  ? messages.pricing.free
                  : formatMoney(priceOf(plan), currency, typed)}
                {priceOf(plan) > 0 && (
                  <span className="ml-1 text-sm font-normal text-muted">
                    {messages.pricing.monthly}
                  </span>
                )}
              </p>
              <p className="mt-2 text-sm text-muted">
                {formatNumber(plan.monthly_credits, typed)}{" "}
                {messages.dashboard.credits.replace("{count} ", "")}
              </p>

              <ul className="mt-5 flex-1 space-y-2 text-sm">
                <li className="flex gap-2">
                  <span aria-hidden className="text-ok">
                    ✓
                  </span>
                  {Math.round(plan.max_upload_bytes / 1024 / 1024)} MB / file
                </li>
                <li className="flex gap-2">
                  <span aria-hidden className="text-ok">
                    ✓
                  </span>
                  {plan.max_pdf_pages} pages / PDF
                </li>
                {plan.features.map((feature) => {
                  const label = FEATURE_LABELS[feature];
                  if (!label) return null;
                  return (
                    <li key={feature} className="flex gap-2">
                      <span aria-hidden className="text-ok">
                        ✓
                      </span>
                      {typed === "ru" ? label.ru : label.en}
                    </li>
                  );
                })}
              </ul>

              <Link
                href={localePath(
                  typed,
                  plan.code === "free" ? "auth/sign-up" : "app/billing",
                )}
                className={`mt-6 ${highlight ? "btn-primary" : "btn-secondary"}`}
              >
                {plan.code === "free"
                  ? messages.pricing.getStarted
                  : messages.pricing.upgrade}
              </Link>
            </section>
          );
        })}
      </div>

      <section className="mt-12" aria-labelledby="credits-heading">
        <h2 id="credits-heading" className="text-2xl font-semibold">
          {messages.pricing.creditsHeading}
        </h2>
        <div className="mt-4 card overflow-hidden">
          <div className="scroll-x">
            <table className="w-full text-sm">
              <thead className="bg-raised text-left">
                <tr>
                  <th scope="col" className="px-4 py-3 font-medium">
                    Operation
                  </th>
                  <th scope="col" className="px-4 py-3 font-medium">
                    Cost
                  </th>
                </tr>
              </thead>
              <tbody>
                {config.credit_rules.map((rule, index) => (
                  <tr key={index} className="border-t border-border">
                    <td className="px-4 py-3">
                      {String(rule.key).replace(/_/g, " ")}
                    </td>
                    <td className="px-4 py-3 tabular-nums">
                      {"multiplier" in rule
                        ? `×${String(rule.multiplier)}`
                        : `${String(rule.credits)} ${
                            "unit" in rule ? `per ${String(rule.unit)}` : ""
                          }`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <p className="mt-3 text-sm text-muted">
          Re-downloading an existing result is free. A job that fails for a
          technical reason is refunded automatically, and cancelling before
          processing starts costs nothing.
        </p>
      </section>
    </div>
  );
}
