"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { ApiError, apiFetch } from "@/lib/api";
import {
  formatBytes,
  formatDate,
  formatMoney,
  formatNumber,
} from "@/lib/format";
import { localePath, type Locale } from "@/lib/i18n";

import { AdminAudit } from "./AdminAudit";
import { AdminFlags } from "./AdminFlags";
import { AdminJobs } from "./AdminJobs";
import { AdminProviders } from "./AdminProviders";
import { AdminStatus } from "./AdminStatus";
import { AdminTickets } from "./AdminTickets";
import { AdminUsers } from "./AdminUsers";

/**
 * Staff panel over the existing admin API.
 *
 * Deliberately client-rendered and never prerendered: every response depends
 * on the caller's admin role, and none of it should end up in a build
 * artefact or a CDN cache.
 */

const SECTIONS = [
  "dashboard",
  "users",
  "jobs",
  "providers",
  "flags",
  "tickets",
  "status",
  "audit",
] as const;

export type AdminSection = (typeof SECTIONS)[number];

const LABELS: Record<AdminSection, string> = {
  dashboard: "Dashboard",
  users: "Users",
  jobs: "Jobs",
  providers: "Providers",
  flags: "Feature flags",
  tickets: "Support",
  status: "Status",
  audit: "Audit log",
};

interface DashboardData {
  period_days: number;
  users: { new: number; active: number };
  jobs: {
    total: number;
    by_status: Record<string, number>;
    success_rate: number | null;
    error_rate: number | null;
    queue_depth: number;
    duration_seconds: { p50: number; p95: number; p99: number; mean: number };
  };
  volume: { pages: number; translation_characters: number };
  money: {
    revenue_minor: number;
    refunds_minor: number;
    provider_cost_usd: number;
    currency: string;
  };
  storage: Record<string, unknown>;
}

export function AdminPanel({ locale }: { locale: Locale }) {
  const router = useRouter();
  const [section, setSection] = useState<AdminSection>("dashboard");
  const [denied, setDenied] = useState(false);

  const onError = useCallback(
    (failure: unknown) => {
      const error = failure as ApiError;
      if (error.status === 401) {
        router.push(localePath(locale, "auth/sign-in"));
        return true;
      }
      if (error.status === 403) {
        setDenied(true);
        return true;
      }
      return false;
    },
    [locale, router],
  );

  if (denied) {
    return (
      <div className="container-page py-16">
        <div className="card mx-auto max-w-md p-8 text-center">
          <h1 className="text-xl font-semibold">Not authorised</h1>
          <p className="mt-2 text-sm text-muted">
            This account does not have an admin role. If that is wrong, ask a
            superadmin to grant one.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="container-page py-8">
      <h1 className="text-2xl font-bold">Admin</h1>

      <nav
        className="mt-6 flex flex-wrap gap-1 border-b border-border"
        aria-label="Admin sections"
      >
        {SECTIONS.map((item) => (
          <button
            key={item}
            type="button"
            onClick={() => setSection(item)}
            aria-current={section === item ? "page" : undefined}
            className={`rounded-t-lg px-3 py-2 text-sm ${
              section === item
                ? "border-b-2 border-accent font-medium text-fg"
                : "text-muted hover:text-fg"
            }`}
          >
            {LABELS[item]}
          </button>
        ))}
      </nav>

      <div className="mt-6">
        {section === "dashboard" && (
          <AdminDashboard locale={locale} onError={onError} />
        )}
        {section === "users" && (
          <AdminUsers locale={locale} onError={onError} />
        )}
        {section === "jobs" && <AdminJobs locale={locale} onError={onError} />}
        {section === "providers" && <AdminProviders onError={onError} />}
        {section === "flags" && <AdminFlags onError={onError} />}
        {section === "tickets" && (
          <AdminTickets locale={locale} onError={onError} />
        )}
        {section === "status" && <AdminStatus onError={onError} />}
        {section === "audit" && (
          <AdminAudit locale={locale} onError={onError} />
        )}
      </div>
    </div>
  );
}

function AdminDashboard({
  locale,
  onError,
}: {
  locale: Locale;
  onError: (failure: unknown) => boolean;
}) {
  const [days, setDays] = useState(7);
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiFetch<DashboardData>(`/api/v1/admin/dashboard?days=${days}`)
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setError(null);
        }
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
  if (!data) return <p className="text-sm text-muted">Loading…</p>;

  const duration = data.jobs.duration_seconds;

  return (
    <div className="grid gap-4">
      <div className="flex items-center gap-2">
        <label className="label mb-0" htmlFor="period">
          Period
        </label>
        <select
          id="period"
          className="input w-auto"
          value={days}
          onChange={(event) => setDays(Number(event.target.value))}
        >
          {[1, 7, 30, 90].map((value) => (
            <option key={value} value={value}>
              {value} days
            </option>
          ))}
        </select>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="New users" value={formatNumber(data.users.new, locale)} />
        <Stat
          label="Active users"
          value={formatNumber(data.users.active, locale)}
        />
        <Stat label="Jobs" value={formatNumber(data.jobs.total, locale)} />
        <Stat
          label="Queue depth"
          value={formatNumber(data.jobs.queue_depth, locale)}
        />
        <Stat
          label="Success rate"
          value={
            data.jobs.success_rate === null
              ? "—"
              : `${(data.jobs.success_rate * 100).toFixed(1)}%`
          }
        />
        <Stat
          label="Error rate"
          value={
            data.jobs.error_rate === null
              ? "—"
              : `${(data.jobs.error_rate * 100).toFixed(1)}%`
          }
        />
        <Stat
          label="Pages processed"
          value={formatNumber(data.volume.pages, locale)}
        />
        <Stat
          label="Translated characters"
          value={formatNumber(data.volume.translation_characters, locale)}
        />
        <Stat
          label="Revenue"
          value={formatMoney(
            data.money.revenue_minor,
            data.money.currency,
            locale,
          )}
        />
        <Stat
          label="Refunds"
          value={formatMoney(
            data.money.refunds_minor,
            data.money.currency,
            locale,
          )}
        />
        <Stat
          label="Provider cost"
          value={`$${data.money.provider_cost_usd.toFixed(2)}`}
        />
        <Stat
          label="Job duration p95"
          value={`${duration.p95}s`}
          hint={`p50 ${duration.p50}s · p99 ${duration.p99}s`}
        />
      </div>

      <div className="card p-5">
        <h2 className="text-sm font-semibold">Jobs by status</h2>
        <div className="mt-3 flex flex-wrap gap-2">
          {Object.entries(data.jobs.by_status).map(([status, count]) => (
            <span key={status} className="chip">
              {status}: {formatNumber(count, locale)}
            </span>
          ))}
          {Object.keys(data.jobs.by_status).length === 0 && (
            <span className="text-sm text-muted">No jobs in this period.</span>
          )}
        </div>
      </div>

      <div className="card p-5">
        <h2 className="text-sm font-semibold">Storage</h2>
        <dl className="mt-3 grid gap-2 sm:grid-cols-3">
          {Object.entries(data.storage).map(([key, value]) => (
            <div key={key}>
              <dt className="text-xs uppercase text-muted">
                {key.replace(/_/g, " ")}
              </dt>
              <dd className="text-sm">
                {typeof value === "number" && key.includes("bytes")
                  ? formatBytes(value, locale)
                  : String(value)}
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}

export function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="card p-4">
      <div className="text-xs uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-1 text-xl font-semibold">{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  );
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <p
      role="alert"
      className="rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger"
    >
      {message}
    </p>
  );
}

export function Timestamp({ iso, locale }: { iso: string; locale: Locale }) {
  return (
    <span className="whitespace-nowrap text-xs text-muted">
      {formatDate(iso, locale)}
    </span>
  );
}

/**
 * Every mutating admin action requires a reason of at least 5 characters —
 * the API rejects anything shorter, so ask for it before sending.
 */
export function useReasonPrompt() {
  return useCallback((action: string): string | null => {
    const reason = window.prompt(
      `Reason for "${action}" (at least 5 characters, recorded in the audit log):`,
    );
    if (reason === null) return null;
    if (reason.trim().length < 5) {
      window.alert("A reason of at least 5 characters is required.");
      return null;
    }
    return reason.trim();
  }, []);
}
