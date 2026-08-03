"use client";

import { useEffect, useState } from "react";

import { ApiError, apiFetch } from "@/lib/api";
import { formatDate, formatNumber } from "@/lib/format";

import { Empty, ErrorNote, Stat, type SectionProps } from "./AccountPanel";

interface Wallet {
  balance: number;
  lifetime_granted: number;
  lifetime_spent: number;
  plan_code: string;
}

interface LedgerEntry {
  id: string;
  delta: number;
  balance_after: number;
  reason: string;
  note: string | null;
  job_id: string | null;
  created_at: string;
}

interface Usage {
  balance: number;
  plan: string;
  [key: string]: unknown;
}

export function AccountUsage({ locale, messages, onError }: SectionProps) {
  const [wallet, setWallet] = useState<Wallet | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [ledger, setLedger] = useState<LedgerEntry[]>([]);
  const [days, setDays] = useState(30);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      apiFetch<Wallet>("/api/v1/account/wallet"),
      apiFetch<Usage>(`/api/v1/account/usage?days=${days}`),
      apiFetch<LedgerEntry[]>("/api/v1/account/wallet/ledger?limit=50"),
    ])
      .then(([walletData, usageData, ledgerData]) => {
        if (cancelled) return;
        setWallet(walletData);
        setUsage(usageData);
        setLedger(ledgerData);
        setError(null);
      })
      .catch((failure) => {
        if (cancelled || onError(failure)) return;
        setError((failure as ApiError).message);
      });
    return () => {
      cancelled = true;
    };
  }, [days, onError]);

  if (error) return <ErrorNote message={error} />;
  if (!wallet)
    return <p className="text-sm text-muted">{messages.common.loading}</p>;

  // The usage summary is an open map so the API can add counters without a
  // breaking change; render whatever numbers it sends beyond the known keys.
  const extra = usage
    ? Object.entries(usage).filter(
        ([key, value]) => typeof value === "number" && key !== "balance",
      )
    : [];

  return (
    <div className="grid gap-4">
      <div className="flex items-center gap-2">
        <label className="label mb-0" htmlFor="usage-period">
          {messages.dashboard.usage}
        </label>
        <select
          id="usage-period"
          className="input w-auto"
          value={days}
          onChange={(event) => setDays(Number(event.target.value))}
        >
          {[7, 30, 90, 365].map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label={messages.dashboard.credits.replace("{count}", "").trim()}
          value={formatNumber(wallet.balance, locale)}
          hint={wallet.plan_code}
        />
        <Stat
          label="Granted"
          value={formatNumber(wallet.lifetime_granted, locale)}
        />
        <Stat
          label="Spent"
          value={formatNumber(wallet.lifetime_spent, locale)}
        />
        {extra.map(([key, value]) => (
          <Stat
            key={key}
            label={key.replace(/_/g, " ")}
            value={formatNumber(value as number, locale)}
          />
        ))}
      </div>

      <div className="card p-5">
        <h2 className="text-sm font-semibold">Credit ledger</h2>
        {ledger.length === 0 ? (
          <p className="mt-3 text-sm text-muted">{messages.dashboard.empty}</p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-muted">
                  <th className="py-2 pr-3 font-medium">Date</th>
                  <th className="py-2 pr-3 font-medium">Reason</th>
                  <th className="py-2 pr-3 text-right font-medium">Change</th>
                  <th className="py-2 text-right font-medium">Balance</th>
                </tr>
              </thead>
              <tbody>
                {ledger.map((entry) => (
                  <tr key={entry.id} className="border-t border-border">
                    <td className="whitespace-nowrap py-2 pr-3 text-xs text-muted">
                      {formatDate(entry.created_at, locale)}
                    </td>
                    <td className="py-2 pr-3">
                      {entry.reason.replace(/_/g, " ")}
                      {entry.note && (
                        <span className="block text-xs text-muted">
                          {entry.note}
                        </span>
                      )}
                    </td>
                    <td
                      className={`py-2 pr-3 text-right tabular-nums ${
                        entry.delta < 0 ? "text-danger" : "text-ok"
                      }`}
                    >
                      {entry.delta > 0 ? "+" : ""}
                      {formatNumber(entry.delta, locale)}
                    </td>
                    <td className="py-2 text-right tabular-nums">
                      {formatNumber(entry.balance_after, locale)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {ledger.length === 0 && extra.length === 0 && (
        <Empty message={messages.dashboard.empty} />
      )}
    </div>
  );
}
