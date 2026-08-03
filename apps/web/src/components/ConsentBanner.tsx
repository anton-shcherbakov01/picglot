"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { captureFirstTouch, consentState, setConsent } from "@/lib/analytics";
import { localePath, type Locale } from "@/lib/i18n";
import type { Messages } from "@/lib/messages";

export function ConsentBanner({
  locale,
  messages,
}: {
  locale: Locale;
  messages: Messages;
}) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    captureFirstTouch();
    if (consentState() === "unknown") setVisible(true);
  }, []);

  if (!visible) return null;

  const choose = (granted: boolean) => {
    setConsent(granted);
    setVisible(false);
  };

  return (
    <div
      role="dialog"
      aria-label={messages.consent.policyLink}
      className="fixed inset-x-0 bottom-0 z-50 border-t border-border bg-surface px-4 py-4 shadow-lg"
    >
      <div className="mx-auto flex max-w-3xl flex-col gap-3 sm:flex-row sm:items-center">
        <p className="flex-1 text-sm text-muted">
          {messages.consent.message}{" "}
          <Link
            href={localePath(locale, "legal/cookies")}
            className="text-accent underline"
          >
            {messages.consent.policyLink}
          </Link>
        </p>
        <div className="flex shrink-0 gap-2">
          <button
            type="button"
            className="btn-secondary text-xs"
            onClick={() => choose(false)}
          >
            {messages.consent.decline}
          </button>
          <button
            type="button"
            className="btn-primary text-xs"
            onClick={() => choose(true)}
          >
            {messages.consent.accept}
          </button>
        </div>
      </div>
    </div>
  );
}
