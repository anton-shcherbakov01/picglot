"use client";

import { useEffect, useState } from "react";

import { ApiError, apiFetch } from "@/lib/api";
import { formatDate } from "@/lib/format";

import {
  ErrorNote,
  Notice,
  OneTimeSecret,
  type SectionProps,
} from "./AccountPanel";

interface Session {
  id: string;
  device_label: string | null;
  created_at: string;
  last_used_at: string | null;
  current: boolean;
}

interface TwoFactorStart {
  secret: string;
  otpauth_url?: string | null;
}

export function AccountSecurity({ locale, messages, onError }: SectionProps) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");

  const [enrolling, setEnrolling] = useState<TwoFactorStart | null>(null);
  const [totpCode, setTotpCode] = useState("");
  const [backupCodes, setBackupCodes] = useState<string | null>(null);

  const loadSessions = () => {
    apiFetch<Session[]>("/api/v1/auth/sessions")
      .then(setSessions)
      .catch((failure) => {
        if (!onError(failure)) setError((failure as ApiError).message);
      });
  };

  useEffect(loadSessions, [onError]);

  const changePassword = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await apiFetch("/api/v1/auth/change-password", {
        method: "POST",
        json: { current_password: currentPassword, new_password: newPassword },
      });
      setCurrentPassword("");
      setNewPassword("");
      setNotice(messages.editor.saved);
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  const startTwoFactor = async () => {
    setError(null);
    try {
      setEnrolling(
        await apiFetch<TwoFactorStart>("/api/v1/auth/2fa/start", {
          method: "POST",
        }),
      );
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    }
  };

  const confirmTwoFactor = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      const result = await apiFetch<{ backup_codes?: string[] } | null>(
        "/api/v1/auth/2fa/confirm",
        {
          method: "POST",
          json: { code: totpCode.trim() },
        },
      );
      setEnrolling(null);
      setTotpCode("");
      if (result?.backup_codes?.length)
        setBackupCodes(result.backup_codes.join("\n"));
      else setNotice(messages.editor.saved);
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  const disableTwoFactor = async () => {
    if (!window.confirm(`${messages.common.confirm}?`)) return;
    try {
      await apiFetch("/api/v1/auth/2fa", { method: "DELETE" });
      setNotice(messages.editor.saved);
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    }
  };

  const revokeSession = async (id: string) => {
    try {
      await apiFetch(`/api/v1/auth/sessions/${id}`, { method: "DELETE" });
      loadSessions();
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    }
  };

  const logoutEverywhere = async () => {
    if (!window.confirm(`${messages.nav.signOut}?`)) return;
    try {
      await apiFetch("/api/v1/auth/logout-all", { method: "POST" });
      window.location.reload();
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    }
  };

  return (
    <div className="grid gap-4">
      {error && <ErrorNote message={error} />}
      {notice && <Notice message={notice} />}

      {backupCodes && (
        <OneTimeSecret
          label="Backup codes — store them somewhere safe"
          value={backupCodes}
          messages={messages}
          onDismiss={() => setBackupCodes(null)}
        />
      )}

      <form
        onSubmit={changePassword}
        className="card grid gap-4 p-5 sm:max-w-lg"
      >
        <h2 className="text-sm font-semibold">{messages.auth.resetTitle}</h2>
        <div>
          <label className="label" htmlFor="current-password">
            {messages.auth.password}
          </label>
          <input
            id="current-password"
            type="password"
            autoComplete="current-password"
            className="input"
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
            required
          />
        </div>
        <div>
          <label className="label" htmlFor="new-password">
            {messages.auth.resetTitle}
          </label>
          <input
            id="new-password"
            type="password"
            autoComplete="new-password"
            className="input"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            required
            minLength={10}
          />
          <p className="mt-1 text-xs text-muted">
            {messages.auth.passwordHint}
          </p>
        </div>
        <button
          type="submit"
          className="btn-primary justify-self-start text-sm"
          disabled={busy}
        >
          {messages.common.save}
        </button>
      </form>

      <div className="card grid gap-3 p-5 sm:max-w-lg">
        <h2 className="text-sm font-semibold">
          {messages.auth.twoFactorTitle}
        </h2>
        {enrolling ? (
          <form onSubmit={confirmTwoFactor} className="grid gap-3">
            <p className="text-sm text-muted">{messages.auth.twoFactorHint}</p>
            <code className="block break-all rounded-lg bg-raised px-3 py-2 text-xs">
              {enrolling.secret}
            </code>
            <div>
              <label className="label" htmlFor="totp-code">
                {messages.auth.twoFactorTitle}
              </label>
              <input
                id="totp-code"
                className="input"
                inputMode="numeric"
                pattern="[0-9]*"
                maxLength={6}
                value={totpCode}
                onChange={(event) => setTotpCode(event.target.value)}
                required
              />
            </div>
            <div className="flex gap-2">
              <button
                type="submit"
                className="btn-primary text-sm"
                disabled={busy}
              >
                {messages.common.confirm}
              </button>
              <button
                type="button"
                className="btn-ghost text-sm"
                onClick={() => setEnrolling(null)}
              >
                {messages.common.cancel}
              </button>
            </div>
          </form>
        ) : (
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="btn-secondary text-sm"
              onClick={startTwoFactor}
            >
              {messages.auth.twoFactorTitle}
            </button>
            <button
              type="button"
              className="btn-ghost text-sm text-danger"
              onClick={disableTwoFactor}
            >
              {messages.common.delete}
            </button>
          </div>
        )}
      </div>

      <div className="card p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-semibold">
            {messages.dashboard.sessions}
          </h2>
          <button
            type="button"
            className="btn-ghost text-xs text-danger"
            onClick={logoutEverywhere}
          >
            {messages.nav.signOut}
          </button>
        </div>
        {sessions.length === 0 ? (
          <p className="mt-3 text-sm text-muted">{messages.dashboard.empty}</p>
        ) : (
          <ul className="mt-3 grid gap-2">
            {sessions.map((session) => (
              <li
                key={session.id}
                className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-2 text-sm first:border-0 first:pt-0"
              >
                <span>
                  {session.device_label ?? "Unknown device"}
                  {session.current && (
                    <span className="chip ml-2">current</span>
                  )}
                  <span className="ml-2 text-xs text-muted">
                    {formatDate(
                      session.last_used_at ?? session.created_at,
                      locale,
                    )}
                  </span>
                </span>
                {!session.current && (
                  <button
                    type="button"
                    className="btn-ghost text-xs text-danger"
                    onClick={() => void revokeSession(session.id)}
                  >
                    {messages.common.delete}
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
