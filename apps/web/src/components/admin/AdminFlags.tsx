"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, apiFetch } from "@/lib/api";

import { ErrorNote } from "./AdminPanel";

interface Flag {
  key: string;
  description: string | null;
  enabled: boolean;
  rollout_percent: number | null;
  plan_codes: string[] | null;
  locales: string[] | null;
  kill_switch: boolean;
}

export function AdminFlags({
  onError,
}: {
  onError: (failure: unknown) => boolean;
}) {
  const [flags, setFlags] = useState<Flag[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const result = await apiFetch<{ items: Flag[] }>(
        "/api/v1/admin/feature-flags",
      );
      setFlags(result.items);
      setError(null);
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    }
  }, [onError]);

  useEffect(() => {
    void load();
  }, [load]);

  const update = async (key: string, patch: Partial<Flag>) => {
    setBusy(key);
    try {
      await apiFetch(`/api/v1/admin/feature-flags/${key}`, {
        method: "PUT",
        json: patch,
      });
      await load();
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    } finally {
      setBusy(null);
    }
  };

  const setRollout = async (flag: Flag) => {
    const raw = window.prompt(
      `Rollout percent for "${flag.key}" (0–100):`,
      String(flag.rollout_percent ?? 100),
    );
    if (raw === null) return;
    const value = Number(raw);
    if (!Number.isInteger(value) || value < 0 || value > 100) {
      window.alert("Enter a whole number between 0 and 100.");
      return;
    }
    await update(flag.key, { rollout_percent: value });
  };

  if (error) return <ErrorNote message={error} />;
  if (!flags) return <p className="text-sm text-muted">Loading…</p>;

  return (
    <div className="grid gap-3">
      <p className="text-xs text-muted">
        A kill switch overrides everything else — when it is on, the feature is
        off regardless of rollout or plan. Changes take effect without a deploy
        and are written to the audit log.
      </p>

      {flags.map((flag) => (
        <div
          key={flag.key}
          className="card flex flex-wrap items-center justify-between gap-3 p-4"
        >
          <div className="min-w-0">
            <div className="font-medium">{flag.key}</div>
            {flag.description && (
              <div className="text-sm text-muted">{flag.description}</div>
            )}
            <div className="mt-1 flex flex-wrap gap-2 text-xs text-muted">
              <span>rollout: {flag.rollout_percent ?? 100}%</span>
              {flag.plan_codes?.length ? (
                <span>plans: {flag.plan_codes.join(", ")}</span>
              ) : null}
              {flag.locales?.length ? (
                <span>locales: {flag.locales.join(", ")}</span>
              ) : null}
              {flag.kill_switch && (
                <span className="text-danger">kill switch on</span>
              )}
            </div>
          </div>

          <div className="flex shrink-0 flex-wrap gap-2">
            <button
              type="button"
              className="btn-secondary text-xs"
              disabled={busy === flag.key}
              onClick={() => void update(flag.key, { enabled: !flag.enabled })}
            >
              {flag.enabled ? "Disable" : "Enable"}
            </button>
            <button
              type="button"
              className="btn-ghost text-xs"
              disabled={busy === flag.key}
              onClick={() => void setRollout(flag)}
            >
              Rollout…
            </button>
            <button
              type="button"
              className="btn-ghost text-xs"
              disabled={busy === flag.key}
              onClick={() =>
                void update(flag.key, { kill_switch: !flag.kill_switch })
              }
            >
              {flag.kill_switch ? "Clear kill switch" : "Kill switch"}
            </button>
          </div>
        </div>
      ))}

      {flags.length === 0 && (
        <p className="text-sm text-muted">No feature flags defined.</p>
      )}
    </div>
  );
}
