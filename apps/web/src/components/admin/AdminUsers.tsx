"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, apiFetch } from "@/lib/api";
import { formatMoney, formatNumber } from "@/lib/format";
import type { Locale } from "@/lib/i18n";

import { ErrorNote, Timestamp, useReasonPrompt } from "./AdminPanel";

interface UserRow {
  id: string;
  email: string;
  name: string | null;
  plan_code: string;
  status: string;
  admin_role: string | null;
  verified: boolean;
  created_at: string;
  last_seen_at: string | null;
}

interface UserList {
  items: UserRow[];
  total: number;
  limit: number;
  offset: number;
}

interface UserDetail {
  user: {
    id: string;
    email: string;
    name: string | null;
    plan_code: string;
    status: string;
    created_at: string;
  };
  credits: {
    balance: number;
    granted: number;
    spent: number;
    ledger_sum: number;
  };
  projects: number;
  jobs: { id: string; type: string; status: string; created_at: string }[];
  payments: {
    id: string;
    amount_minor: number;
    currency: string;
    status: string;
    created_at: string;
  }[];
  note: string;
}

export function AdminUsers({
  locale,
  onError,
}: {
  locale: Locale;
  onError: (failure: unknown) => boolean;
}) {
  const [search, setSearch] = useState("");
  const [list, setList] = useState<UserList | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<UserDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const askReason = useReasonPrompt();

  const load = useCallback(async () => {
    try {
      const query = new URLSearchParams({ limit: "25" });
      if (search) query.set("search", search);
      setList(await apiFetch<UserList>(`/api/v1/admin/users?${query}`));
      setError(null);
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    }
  }, [search, onError]);

  useEffect(() => {
    const timer = setTimeout(() => void load(), search ? 300 : 0);
    return () => clearTimeout(timer);
  }, [load, search]);

  const openDetail = async (id: string) => {
    setSelected(id);
    setDetail(null);
    try {
      setDetail(await apiFetch<UserDetail>(`/api/v1/admin/users/${id}`));
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    }
  };

  const adjustCredits = async (id: string) => {
    const raw = window.prompt(
      "Credit adjustment (positive to grant, negative to deduct):",
    );
    if (raw === null) return;
    const delta = Number(raw);
    if (!Number.isInteger(delta) || delta === 0) {
      window.alert("Enter a non-zero whole number.");
      return;
    }
    const reason = askReason("adjust credits");
    if (!reason) return;
    try {
      await apiFetch(`/api/v1/admin/users/${id}/credits`, {
        method: "POST",
        json: { delta, reason },
      });
      await openDetail(id);
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    }
  };

  const setStatus = async (id: string, status: "active" | "suspended") => {
    const reason = askReason(`set status to ${status}`);
    if (!reason) return;
    try {
      await apiFetch(`/api/v1/admin/users/${id}/status`, {
        method: "POST",
        json: { status, reason },
      });
      await Promise.all([load(), openDetail(id)]);
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    }
  };

  return (
    <div className="grid gap-4">
      {error && <ErrorNote message={error} />}

      <input
        className="input max-w-sm"
        placeholder="Search by email"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        aria-label="Search users by email"
      />

      <div className="card overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-border text-xs uppercase text-muted">
            <tr>
              <th className="p-3">Email</th>
              <th className="p-3">Plan</th>
              <th className="p-3">Status</th>
              <th className="p-3">Role</th>
              <th className="p-3">Created</th>
              <th className="p-3" />
            </tr>
          </thead>
          <tbody>
            {list?.items.map((row) => (
              <tr
                key={row.id}
                className="border-b border-border/50 last:border-0"
              >
                <td className="p-3">
                  {row.email}
                  {!row.verified && (
                    <span className="ml-2 chip text-xs">unverified</span>
                  )}
                </td>
                <td className="p-3">{row.plan_code}</td>
                <td className="p-3">
                  <span
                    className={row.status === "suspended" ? "text-danger" : ""}
                  >
                    {row.status}
                  </span>
                </td>
                <td className="p-3">{row.admin_role ?? "—"}</td>
                <td className="p-3">
                  <Timestamp iso={row.created_at} locale={locale} />
                </td>
                <td className="p-3 text-right">
                  <button
                    type="button"
                    className="btn-ghost text-xs"
                    onClick={() => void openDetail(row.id)}
                  >
                    Open
                  </button>
                </td>
              </tr>
            ))}
            {list?.items.length === 0 && (
              <tr>
                <td className="p-4 text-muted" colSpan={6}>
                  No users match.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {list && (
        <p className="text-xs text-muted">
          Showing {list.items.length} of {formatNumber(list.total, locale)}
        </p>
      )}

      {selected && (
        <div className="card p-5">
          <div className="flex items-start justify-between gap-4">
            <h2 className="text-sm font-semibold">User detail</h2>
            <button
              type="button"
              className="btn-ghost text-xs"
              onClick={() => {
                setSelected(null);
                setDetail(null);
              }}
            >
              Close
            </button>
          </div>

          {!detail ? (
            <p className="mt-3 text-sm text-muted">Loading…</p>
          ) : (
            <div className="mt-4 grid gap-4">
              <div className="grid gap-2 sm:grid-cols-2">
                <Field label="Email" value={detail.user.email} />
                <Field label="Name" value={detail.user.name ?? "—"} />
                <Field label="Plan" value={detail.user.plan_code} />
                <Field label="Status" value={detail.user.status} />
                <Field label="Projects" value={String(detail.projects)} />
                <Field
                  label="Credits"
                  value={`${detail.credits.balance} (granted ${detail.credits.granted}, spent ${detail.credits.spent})`}
                />
              </div>

              {detail.credits.balance !== detail.credits.ledger_sum && (
                <ErrorNote
                  message={`Wallet balance ${detail.credits.balance} does not match the ledger sum ${detail.credits.ledger_sum}. Investigate before adjusting.`}
                />
              )}

              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  className="btn-secondary text-xs"
                  onClick={() => void adjustCredits(detail.user.id)}
                >
                  Adjust credits
                </button>
                {detail.user.status === "suspended" ? (
                  <button
                    type="button"
                    className="btn-secondary text-xs"
                    onClick={() => void setStatus(detail.user.id, "active")}
                  >
                    Reactivate
                  </button>
                ) : (
                  <button
                    type="button"
                    className="btn-secondary text-xs"
                    onClick={() => void setStatus(detail.user.id, "suspended")}
                  >
                    Suspend
                  </button>
                )}
              </div>

              <div>
                <h3 className="text-xs uppercase text-muted">Recent jobs</h3>
                <ul className="mt-2 grid gap-1 text-sm">
                  {detail.jobs.map((job) => (
                    <li key={job.id} className="flex justify-between gap-3">
                      <span>
                        {job.type} · {job.status}
                      </span>
                      <Timestamp iso={job.created_at} locale={locale} />
                    </li>
                  ))}
                  {detail.jobs.length === 0 && (
                    <li className="text-muted">None.</li>
                  )}
                </ul>
              </div>

              <div>
                <h3 className="text-xs uppercase text-muted">Payments</h3>
                <ul className="mt-2 grid gap-1 text-sm">
                  {detail.payments.map((payment) => (
                    <li key={payment.id} className="flex justify-between gap-3">
                      <span>
                        {formatMoney(
                          payment.amount_minor,
                          payment.currency,
                          locale,
                        )}{" "}
                        · {payment.status}
                      </span>
                      <Timestamp iso={payment.created_at} locale={locale} />
                    </li>
                  ))}
                  {detail.payments.length === 0 && (
                    <li className="text-muted">None.</li>
                  )}
                </ul>
              </div>

              <p className="text-xs text-muted">{detail.note}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs uppercase text-muted">{label}</div>
      <div className="text-sm">{value}</div>
    </div>
  );
}
