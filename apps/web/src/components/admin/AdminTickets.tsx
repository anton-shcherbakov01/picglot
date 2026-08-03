"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, apiFetch } from "@/lib/api";
import type { Locale } from "@/lib/i18n";

import { ErrorNote, Timestamp } from "./AdminPanel";

interface Ticket {
  id: string;
  email: string;
  category: string;
  subject: string;
  message: string;
  project_id: string | null;
  request_id: string | null;
  created_at: string;
}

export function AdminTickets({
  locale,
  onError,
}: {
  locale: Locale;
  onError: (failure: unknown) => boolean;
}) {
  const [status, setStatus] = useState("open");
  const [tickets, setTickets] = useState<Ticket[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const result = await apiFetch<{ items: Ticket[] }>(
        `/api/v1/admin/support/tickets?ticket_status=${encodeURIComponent(status)}&limit=25`,
      );
      setTickets(result.items);
      setError(null);
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    }
  }, [status, onError]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) return <ErrorNote message={error} />;

  return (
    <div className="grid gap-4">
      <div>
        <label className="label" htmlFor="ticket-status">
          Status
        </label>
        <select
          id="ticket-status"
          className="input w-auto"
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          {["open", "closed"].map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
      </div>

      {!tickets ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : tickets.length === 0 ? (
        <p className="text-sm text-muted">No {status} tickets.</p>
      ) : (
        <div className="grid gap-3">
          {tickets.map((ticket) => (
            <div key={ticket.id} className="card p-4">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="font-medium">{ticket.subject}</span>
                <Timestamp iso={ticket.created_at} locale={locale} />
              </div>
              <div className="mt-1 flex flex-wrap gap-2 text-xs text-muted">
                <span className="chip">{ticket.category}</span>
                <span>{ticket.email}</span>
                {ticket.project_id && <span>project: {ticket.project_id}</span>}
                {ticket.request_id && <span>request: {ticket.request_id}</span>}
              </div>
              <p className="mt-3 whitespace-pre-wrap text-sm">
                {ticket.message}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
