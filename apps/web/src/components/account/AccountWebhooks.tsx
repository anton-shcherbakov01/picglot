"use client";

import { useEffect, useState } from "react";

import { ApiError, apiFetch } from "@/lib/api";
import { formatDate } from "@/lib/format";

import { ErrorNote, OneTimeSecret, type SectionProps } from "./AccountPanel";

interface Webhook {
  id: string;
  url: string;
  events: string[];
  description: string | null;
  is_active: boolean;
  consecutive_failures: number;
  created_at: string;
}

interface WebhookCreated extends Webhook {
  secret: string;
}

interface Delivery {
  id: string;
  event: string;
  event_id: string;
  status: string;
  attempt: number;
  response_status: number | null;
  duration_ms: number | null;
  created_at: string;
  delivered_at: string | null;
}

const EVENTS = [
  "job.completed",
  "job.failed",
  "batch.completed",
  "export.ready",
];

export function AccountWebhooks({ locale, messages, onError }: SectionProps) {
  const [hooks, setHooks] = useState<Webhook[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [secret, setSecret] = useState<{ label: string; value: string } | null>(
    null,
  );
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState<string[]>([EVENTS[0]!]);
  const [busy, setBusy] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);

  const load = () => {
    apiFetch<Webhook[]>("/api/v1/account/webhooks")
      .then((rows) => {
        setHooks(rows);
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
      const hook = await apiFetch<WebhookCreated>("/api/v1/account/webhooks", {
        method: "POST",
        json: { url: url.trim(), events },
      });
      setSecret({ label: hook.url, value: hook.secret });
      setUrl("");
      load();
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  const rotate = async (id: string) => {
    try {
      const result = await apiFetch<{ secret: string }>(
        `/api/v1/account/webhooks/${id}/rotate`,
        { method: "POST" },
      );
      setSecret({ label: messages.dashboard.webhooks, value: result.secret });
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    }
  };

  const remove = async (id: string) => {
    if (!window.confirm(`${messages.common.delete}?`)) return;
    try {
      await apiFetch(`/api/v1/account/webhooks/${id}`, { method: "DELETE" });
      if (openId === id) setOpenId(null);
      load();
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    }
  };

  const toggleDeliveries = async (id: string) => {
    if (openId === id) {
      setOpenId(null);
      return;
    }
    try {
      const rows = await apiFetch<Delivery[]>(
        `/api/v1/account/webhooks/${id}/deliveries`,
      );
      setDeliveries(rows);
      setOpenId(id);
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    }
  };

  const replay = async (deliveryId: string) => {
    try {
      const updated = await apiFetch<Delivery>(
        `/api/v1/account/webhooks/deliveries/${deliveryId}/replay`,
        { method: "POST" },
      );
      setDeliveries((rows) =>
        rows.map((row) => (row.id === updated.id ? updated : row)),
      );
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    }
  };

  return (
    <div className="grid gap-4">
      {error && <ErrorNote message={error} />}

      {secret && (
        <OneTimeSecret
          label={`${secret.label} — signing secret`}
          value={secret.value}
          messages={messages}
          onDismiss={() => setSecret(null)}
        />
      )}

      <form onSubmit={create} className="card grid gap-3 p-5">
        <div>
          <label className="label" htmlFor="hook-url">
            URL
          </label>
          <input
            id="hook-url"
            type="url"
            className="input"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://example.com/hooks/picglot"
            required
          />
        </div>
        <fieldset>
          <legend className="label">Events</legend>
          <div className="flex flex-wrap gap-3">
            {EVENTS.map((name) => (
              <label key={name} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={events.includes(name)}
                  onChange={(changed) =>
                    setEvents((current) =>
                      changed.target.checked
                        ? [...current, name]
                        : current.filter((item) => item !== name),
                    )
                  }
                />
                {name}
              </label>
            ))}
          </div>
        </fieldset>
        <button
          type="submit"
          className="btn-primary justify-self-start text-sm"
          disabled={busy || events.length === 0}
        >
          {messages.dashboard.newProject}
        </button>
      </form>

      {hooks.length === 0 ? (
        <p className="text-sm text-muted">{messages.dashboard.empty}</p>
      ) : (
        hooks.map((hook) => (
          <div key={hook.id} className="card p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate font-medium">{hook.url}</p>
                <p className="mt-1 text-xs text-muted">
                  {hook.events.join(", ")} ·{" "}
                  {formatDate(hook.created_at, locale)}
                  {hook.consecutive_failures > 0 && (
                    <span className="ml-2 text-danger">
                      {hook.consecutive_failures} consecutive failures
                    </span>
                  )}
                  {!hook.is_active && (
                    <span className="ml-2 text-warn">inactive</span>
                  )}
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                <button
                  type="button"
                  className="btn-ghost text-xs"
                  onClick={() => void toggleDeliveries(hook.id)}
                >
                  {openId === hook.id ? messages.common.close : "Deliveries"}
                </button>
                <button
                  type="button"
                  className="btn-ghost text-xs"
                  onClick={() => void rotate(hook.id)}
                >
                  Rotate secret
                </button>
                <button
                  type="button"
                  className="btn-ghost text-xs text-danger"
                  onClick={() => void remove(hook.id)}
                >
                  {messages.common.delete}
                </button>
              </div>
            </div>

            {openId === hook.id && (
              <div className="mt-4 overflow-x-auto border-t border-border pt-3">
                {deliveries.length === 0 ? (
                  <p className="text-sm text-muted">
                    {messages.dashboard.empty}
                  </p>
                ) : (
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs uppercase text-muted">
                        <th className="py-2 pr-3 font-medium">Event</th>
                        <th className="py-2 pr-3 font-medium">Status</th>
                        <th className="py-2 pr-3 font-medium">Attempt</th>
                        <th className="py-2 pr-3 font-medium">Sent</th>
                        <th className="py-2 font-medium" />
                      </tr>
                    </thead>
                    <tbody>
                      {deliveries.map((delivery) => (
                        <tr
                          key={delivery.id}
                          className="border-t border-border"
                        >
                          <td className="py-2 pr-3">{delivery.event}</td>
                          <td className="py-2 pr-3">
                            {delivery.status}
                            {delivery.response_status !== null && (
                              <span className="text-muted">
                                {" "}
                                ({delivery.response_status})
                              </span>
                            )}
                          </td>
                          <td className="py-2 pr-3 tabular-nums">
                            {delivery.attempt}
                          </td>
                          <td className="whitespace-nowrap py-2 pr-3 text-xs text-muted">
                            {delivery.delivered_at
                              ? formatDate(delivery.delivered_at, locale)
                              : formatDate(delivery.created_at, locale)}
                          </td>
                          <td className="py-2 text-right">
                            <button
                              type="button"
                              className="btn-ghost text-xs"
                              onClick={() => void replay(delivery.id)}
                            >
                              {messages.processing.retry}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}
          </div>
        ))
      )}
    </div>
  );
}
