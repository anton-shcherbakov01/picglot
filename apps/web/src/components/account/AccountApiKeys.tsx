"use client";

import { useEffect, useState } from "react";

import { ApiError, apiFetch } from "@/lib/api";
import { formatDate } from "@/lib/format";

import { ErrorNote, OneTimeSecret, type SectionProps } from "./AccountPanel";

interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  last_four: string;
  scopes: string[];
  test_mode: boolean;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked: boolean;
}

interface ApiKeyCreated extends ApiKey {
  key: string;
}

export function AccountApiKeys({ locale, messages, onError }: SectionProps) {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const [name, setName] = useState("");
  const [testMode, setTestMode] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = () => {
    apiFetch<ApiKey[]>("/api/v1/account/api-keys")
      .then((rows) => {
        setKeys(rows);
        setError(null);
      })
      .catch((failure) => {
        if (!onError(failure)) setError((failure as ApiError).message);
      });
  };

  useEffect(load, [onError]);

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const key = await apiFetch<ApiKeyCreated>("/api/v1/account/api-keys", {
        method: "POST",
        json: { name: name.trim(), test_mode: testMode },
      });
      setCreated(key);
      setName("");
      load();
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (id: string) => {
    if (!window.confirm(`${messages.common.delete}?`)) return;
    try {
      await apiFetch(`/api/v1/account/api-keys/${id}`, { method: "DELETE" });
      load();
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    }
  };

  return (
    <div className="grid gap-4">
      {error && <ErrorNote message={error} />}

      {created && (
        <OneTimeSecret
          label={`${created.name} — ${messages.dashboard.apiKeys}`}
          value={created.key}
          messages={messages}
          onDismiss={() => setCreated(null)}
        />
      )}

      <form
        onSubmit={create}
        className="card flex flex-wrap items-end gap-3 p-5"
      >
        <div className="min-w-48 flex-1">
          <label className="label" htmlFor="key-name">
            {messages.editor.title}
          </label>
          <input
            id="key-name"
            className="input"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
            minLength={2}
            maxLength={64}
          />
        </div>
        <label className="flex items-center gap-2 pb-2 text-sm">
          <input
            type="checkbox"
            checked={testMode}
            onChange={(event) => setTestMode(event.target.checked)}
          />
          Test mode
        </label>
        <button type="submit" className="btn-primary text-sm" disabled={busy}>
          {messages.dashboard.newProject}
        </button>
      </form>

      <div className="card p-5">
        {keys.length === 0 ? (
          <p className="text-sm text-muted">{messages.dashboard.empty}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-muted">
                  <th className="py-2 pr-3 font-medium">Name</th>
                  <th className="py-2 pr-3 font-medium">Key</th>
                  <th className="py-2 pr-3 font-medium">Created</th>
                  <th className="py-2 pr-3 font-medium">Last used</th>
                  <th className="py-2 font-medium" />
                </tr>
              </thead>
              <tbody>
                {keys.map((key) => (
                  <tr key={key.id} className="border-t border-border">
                    <td className="py-2 pr-3">
                      {key.name}
                      {key.test_mode && <span className="chip ml-2">test</span>}
                      {key.revoked && (
                        <span className="chip ml-2">revoked</span>
                      )}
                    </td>
                    <td className="py-2 pr-3 font-mono text-xs">
                      {key.prefix}…{key.last_four}
                    </td>
                    <td className="whitespace-nowrap py-2 pr-3 text-xs text-muted">
                      {formatDate(key.created_at, locale)}
                    </td>
                    <td className="whitespace-nowrap py-2 pr-3 text-xs text-muted">
                      {key.last_used_at
                        ? formatDate(key.last_used_at, locale)
                        : "—"}
                    </td>
                    <td className="py-2 text-right">
                      {!key.revoked && (
                        <button
                          type="button"
                          className="btn-ghost text-xs text-danger"
                          onClick={() => void revoke(key.id)}
                        >
                          {messages.common.delete}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
