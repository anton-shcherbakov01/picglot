"use client";

import { useEffect, useState } from "react";

import { ApiError, apiFetch } from "@/lib/api";
import { LOCALES, LOCALE_NAMES } from "@/lib/i18n";

import { ErrorNote, Notice, type SectionProps } from "./AccountPanel";

interface User {
  id: string;
  email: string;
  name: string | null;
  locale: string;
  timezone: string;
  plan_code: string;
  status: string;
  email_verified: boolean;
}

export function AccountProfile({ messages, onError }: SectionProps) {
  const [user, setUser] = useState<User | null>(null);
  const [name, setName] = useState("");
  const [locale, setLocale] = useState("en");
  const [timezone, setTimezone] = useState("UTC");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    apiFetch<User>("/api/v1/auth/me")
      .then((data) => {
        setUser(data);
        setName(data.name ?? "");
        setLocale(data.locale);
        setTimezone(data.timezone);
      })
      .catch((failure) => {
        if (!onError(failure)) setError((failure as ApiError).message);
      });
  }, [onError]);

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await apiFetch<User>("/api/v1/account/profile", {
        method: "PATCH",
        json: { name: name.trim() || null, locale, timezone },
      });
      setUser(updated);
      setNotice(messages.editor.saved);
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  const resendVerification = async () => {
    try {
      await apiFetch("/api/v1/auth/resend-verification", { method: "POST" });
      setNotice(messages.auth.magicLinkSent);
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    }
  };

  if (!user) {
    return error ? (
      <ErrorNote message={error} />
    ) : (
      <p className="text-sm text-muted">{messages.common.loading}</p>
    );
  }

  return (
    <div className="grid gap-4">
      {error && <ErrorNote message={error} />}
      {notice && <Notice message={notice} />}

      {!user.email_verified && (
        <div className="card border-warn/50 p-4">
          <p className="text-sm">{messages.errors.email_not_verified}</p>
          <button
            type="button"
            className="btn-secondary mt-3 text-xs"
            onClick={resendVerification}
          >
            {messages.auth.verifyTitle}
          </button>
        </div>
      )}

      <form onSubmit={save} className="card grid gap-4 p-5 sm:max-w-lg">
        <div>
          <label className="label" htmlFor="profile-email">
            {messages.auth.email}
          </label>
          <input
            id="profile-email"
            className="input"
            value={user.email}
            disabled
            readOnly
          />
        </div>

        <div>
          <label className="label" htmlFor="profile-name">
            {messages.auth.name}
          </label>
          <input
            id="profile-name"
            className="input"
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={120}
          />
        </div>

        <div>
          <label className="label" htmlFor="profile-locale">
            {messages.nav.languages}
          </label>
          <select
            id="profile-locale"
            className="input"
            value={locale}
            onChange={(event) => setLocale(event.target.value)}
          >
            {LOCALES.map((code) => (
              <option key={code} value={code}>
                {LOCALE_NAMES[code]}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="label" htmlFor="profile-timezone">
            Timezone
          </label>
          <input
            id="profile-timezone"
            className="input"
            value={timezone}
            onChange={(event) => setTimezone(event.target.value)}
            placeholder="Europe/Moscow"
          />
        </div>

        <button
          type="submit"
          className="btn-primary justify-self-start text-sm"
          disabled={busy}
        >
          {busy ? messages.editor.saving : messages.common.save}
        </button>
      </form>
    </div>
  );
}
