"use client";

import { useEffect, useState } from "react";

import { ApiError, apiFetch, type AppConfig } from "@/lib/api";
import { localePath } from "@/lib/i18n";

import { ErrorNote, Notice, type SectionProps } from "./AccountPanel";

/**
 * Data and retention. Export and deletion are the two things a user is
 * entitled to be able to do without asking anyone, so both are one click.
 */
export function AccountData({ locale, messages, onError }: SectionProps) {
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const [config, setConfig] = useState<AppConfig | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiFetch<AppConfig>("/api/v1/config")
      .then((data) => {
        if (!cancelled) setConfig(data);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const exportData = async () => {
    setBusy(true);
    setError(null);
    try {
      const payload = await apiFetch<unknown>("/api/v1/account/export-data");
      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "picglot-account-export.json";
      anchor.click();
      URL.revokeObjectURL(url);
      setNotice(messages.result.download);
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  const deleteAccount = async () => {
    if (confirmText.trim().toLowerCase() !== "delete") return;
    if (!window.confirm(`${messages.dashboard.deleteForever}?`)) return;
    setBusy(true);
    try {
      await apiFetch("/api/v1/account", { method: "DELETE" });
      window.location.href = localePath(locale);
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
      setBusy(false);
    }
  };

  const retention = config?.limits?.["retention_hours"] as
    Record<string, number> | undefined;

  return (
    <div className="grid gap-4">
      {error && <ErrorNote message={error} />}
      {notice && <Notice message={notice} />}

      <div className="card p-5">
        <h2 className="text-sm font-semibold">
          {messages.dashboard.dataRetention}
        </h2>
        <ul className="mt-3 grid list-disc gap-1 pl-5 text-sm text-muted">
          {messages.home.securityPoints.map((point) => (
            <li key={point}>{point}</li>
          ))}
        </ul>
        {retention && (
          <dl className="mt-4 grid gap-2 sm:grid-cols-4">
            {Object.entries(retention).map(([plan, hours]) => (
              <div key={plan}>
                <dt className="text-xs uppercase text-muted">{plan}</dt>
                <dd className="text-sm">{Math.round(hours / 24)} d</dd>
              </div>
            ))}
          </dl>
        )}
      </div>

      <div className="card p-5">
        <h2 className="text-sm font-semibold">{messages.result.download}</h2>
        <p className="mt-2 text-sm text-muted">
          Everything this account holds, as one JSON file.
        </p>
        <button
          type="button"
          className="btn-secondary mt-3 text-sm"
          disabled={busy}
          onClick={exportData}
        >
          {messages.result.download}
        </button>
      </div>

      <div className="card border-danger/40 p-5">
        <h2 className="text-sm font-semibold text-danger">
          {messages.dashboard.deleteForever}
        </h2>
        <p className="mt-2 text-sm text-muted">
          Deletes the account, its projects and their files. Billing records are
          kept only where tax law requires. This cannot be undone.
        </p>
        <label className="label mt-4" htmlFor="delete-confirm">
          Type <code>delete</code> to confirm
        </label>
        <input
          id="delete-confirm"
          className="input sm:max-w-xs"
          value={confirmText}
          onChange={(event) => setConfirmText(event.target.value)}
          autoComplete="off"
        />
        <button
          type="button"
          className="btn-primary mt-3 bg-danger text-sm hover:bg-danger"
          disabled={busy || confirmText.trim().toLowerCase() !== "delete"}
          onClick={deleteAccount}
        >
          {messages.dashboard.deleteForever}
        </button>
      </div>
    </div>
  );
}
