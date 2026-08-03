"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, apiFetch } from "@/lib/api";

import { ErrorNote } from "./AdminPanel";

interface Incident {
  id: string;
  component: string;
  severity: string;
  title: string;
  body: string | null;
  started_at?: string;
}

interface StatusResponse {
  incidents?: Incident[];
  components?: { key: string; status: string }[];
}

const SEVERITIES = [
  "degraded",
  "partial_outage",
  "major_outage",
  "maintenance",
];
const COMPONENTS = [
  "api",
  "web",
  "workers",
  "storage",
  "ocr",
  "translation",
  "billing",
];

export function AdminStatus({
  onError,
}: {
  onError: (failure: unknown) => boolean;
}) {
  const [current, setCurrent] = useState<StatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setCurrent(await apiFetch<StatusResponse>("/api/v1/status"));
      setError(null);
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    }
  }, [onError]);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    try {
      await apiFetch("/api/v1/admin/status/incidents", {
        method: "POST",
        json: {
          component: form.get("component"),
          severity: form.get("severity"),
          title: form.get("title"),
          body: form.get("body") || null,
          is_scheduled: form.get("is_scheduled") === "on",
        },
      });
      (event.target as HTMLFormElement).reset();
      await load();
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  const resolve = async (id: string) => {
    setBusy(true);
    try {
      await apiFetch(`/api/v1/admin/status/incidents/${id}/resolve`, {
        method: "POST",
      });
      await load();
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  const incidents = current?.incidents ?? [];

  return (
    <div className="grid gap-4">
      {error && <ErrorNote message={error} />}

      <div className="card p-5">
        <h2 className="text-sm font-semibold">Open incidents</h2>
        {incidents.length === 0 ? (
          <p className="mt-2 text-sm text-muted">
            None — the public status page reports all clear.
          </p>
        ) : (
          <ul className="mt-3 grid gap-2">
            {incidents.map((incident) => (
              <li
                key={incident.id}
                className="flex flex-wrap items-center justify-between gap-3"
              >
                <div>
                  <span className="font-medium">{incident.title}</span>
                  <span className="ml-2 chip text-xs">
                    {incident.component}
                  </span>
                  <span className="ml-1 chip text-xs">{incident.severity}</span>
                  {incident.body && (
                    <p className="mt-1 text-sm text-muted">{incident.body}</p>
                  )}
                </div>
                <button
                  type="button"
                  className="btn-secondary text-xs"
                  disabled={busy}
                  onClick={() => void resolve(incident.id)}
                >
                  Resolve
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <form onSubmit={create} className="card grid gap-3 p-5">
        <h2 className="text-sm font-semibold">Open an incident</h2>
        <p className="text-xs text-muted">
          This is what users see on the public status page. Say what is affected
          and what you are doing about it — not an internal cause.
        </p>

        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor="component">
              Component
            </label>
            <select
              id="component"
              name="component"
              className="input"
              defaultValue="api"
            >
              {COMPONENTS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label" htmlFor="severity">
              Severity
            </label>
            <select
              id="severity"
              name="severity"
              className="input"
              defaultValue="degraded"
            >
              {SEVERITIES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div>
          <label className="label" htmlFor="title">
            Title
          </label>
          <input
            id="title"
            name="title"
            className="input"
            required
            maxLength={255}
          />
        </div>

        <div>
          <label className="label" htmlFor="body">
            Detail (optional)
          </label>
          <textarea id="body" name="body" className="input min-h-24" />
        </div>

        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" name="is_scheduled" />
          Scheduled maintenance rather than an unplanned incident
        </label>

        <button
          type="submit"
          className="btn-primary justify-self-start text-sm"
          disabled={busy}
        >
          {busy ? "…" : "Publish incident"}
        </button>
      </form>
    </div>
  );
}
