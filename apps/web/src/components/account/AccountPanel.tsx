"use client";

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";

import type { ApiError } from "@/lib/api";
import { localePath, type Locale } from "@/lib/i18n";
import type { Messages } from "@/lib/messages";

import { AccountApiKeys } from "./AccountApiKeys";
import { AccountBilling } from "./AccountBilling";
import { AccountData } from "./AccountData";
import { AccountGlossaries } from "./AccountGlossaries";
import { AccountMemory } from "./AccountMemory";
import { AccountProfile } from "./AccountProfile";
import { AccountSecurity } from "./AccountSecurity";
import { AccountUsage } from "./AccountUsage";
import { AccountWebhooks } from "./AccountWebhooks";

/**
 * Account area over the existing `/api/v1/account`, `/billing` and `/auth`
 * endpoints. Client-rendered and noindex: every response is specific to the
 * signed-in user and must never reach a build artefact or a CDN cache.
 */

const SECTIONS = [
  "usage",
  "billing",
  "apiKeys",
  "webhooks",
  "glossary",
  "memory",
  "profile",
  "security",
  "data",
] as const;

export type AccountSection = (typeof SECTIONS)[number];

export function AccountPanel({
  locale,
  messages,
  initialSection = "usage",
}: {
  locale: Locale;
  messages: Messages;
  initialSection?: AccountSection;
}) {
  const router = useRouter();
  const [section, setSection] = useState<AccountSection>(initialSection);

  const labels: Record<AccountSection, string> = {
    usage: messages.dashboard.usage,
    billing: messages.dashboard.billing,
    apiKeys: messages.dashboard.apiKeys,
    webhooks: messages.dashboard.webhooks,
    glossary: messages.dashboard.glossary,
    memory: messages.dashboard.memory,
    profile: messages.dashboard.profile,
    security: messages.nav.security,
    data: messages.dashboard.dataRetention,
  };

  /** An expired session should land on sign-in, not on a wall of red text. */
  const onError = useCallback(
    (failure: unknown) => {
      if ((failure as ApiError).status === 401) {
        router.push(localePath(locale, "auth/sign-in"));
        return true;
      }
      return false;
    },
    [locale, router],
  );

  const shared = { locale, messages, onError };

  return (
    <div className="container-page py-8">
      <h1 className="text-2xl font-bold">{messages.nav.account}</h1>

      <nav
        className="mt-6 flex flex-wrap gap-1 border-b border-border"
        aria-label={messages.nav.account}
      >
        {SECTIONS.map((item) => (
          <button
            key={item}
            type="button"
            onClick={() => setSection(item)}
            aria-current={section === item ? "page" : undefined}
            className={`rounded-t-lg px-3 py-2 text-sm ${
              section === item
                ? "border-b-2 border-accent font-medium text-fg"
                : "text-muted hover:text-fg"
            }`}
          >
            {labels[item]}
          </button>
        ))}
      </nav>

      <div className="mt-6">
        {section === "usage" && <AccountUsage {...shared} />}
        {section === "billing" && <AccountBilling {...shared} />}
        {section === "apiKeys" && <AccountApiKeys {...shared} />}
        {section === "webhooks" && <AccountWebhooks {...shared} />}
        {section === "glossary" && <AccountGlossaries {...shared} />}
        {section === "memory" && <AccountMemory {...shared} />}
        {section === "profile" && <AccountProfile {...shared} />}
        {section === "security" && <AccountSecurity {...shared} />}
        {section === "data" && <AccountData {...shared} />}
      </div>
    </div>
  );
}

export interface SectionProps {
  locale: Locale;
  messages: Messages;
  onError: (failure: unknown) => boolean;
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <p
      role="alert"
      className="rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger"
    >
      {message}
    </p>
  );
}

export function Notice({ message }: { message: string }) {
  return (
    <p
      role="status"
      className="rounded-lg border border-ok/40 bg-ok/10 px-3 py-2 text-sm text-ok"
    >
      {message}
    </p>
  );
}

export function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="card p-4">
      <div className="text-xs uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-1 text-xl font-semibold">{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  );
}

export function Empty({ message }: { message: string }) {
  return <p className="text-sm text-muted">{message}</p>;
}

/**
 * A secret the API returns exactly once. Keep it on screen until the user
 * dismisses it — re-fetching the list will never show it again.
 */
export function OneTimeSecret({
  label,
  value,
  onDismiss,
  messages,
}: {
  label: string;
  value: string;
  onDismiss: () => void;
  messages: Messages;
}) {
  return (
    <div className="card border-accent/50 p-4">
      <h3 className="text-sm font-semibold">{label}</h3>
      <code className="mt-2 block break-all rounded-lg bg-raised px-3 py-2 text-xs">
        {value}
      </code>
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          className="btn-secondary text-xs"
          onClick={() => void navigator.clipboard?.writeText(value)}
        >
          {messages.result.copy}
        </button>
        <button type="button" className="btn-ghost text-xs" onClick={onDismiss}>
          {messages.common.close}
        </button>
      </div>
    </div>
  );
}
