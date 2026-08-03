'use client';

import { useEffect, useState } from 'react';

import { ApiError, apiFetch } from '@/lib/api';

import { ErrorNote } from './AdminPanel';

interface ProvidersData {
  health: Record<string, Record<string, unknown>>;
  usage_24h: Record<
    string,
    { calls: number; cost_usd: number; avg_latency_ms: number; success_rate: number }
  >;
  configuration: {
    kind: string;
    name: string;
    enabled: boolean;
    priority: number;
    health_status: string | null;
    health_detail: string | null;
    daily_cost_limit_usd: number | null;
    has_secrets: boolean;
  }[];
}

export function AdminProviders({ onError }: { onError: (failure: unknown) => boolean }) {
  const [data, setData] = useState<ProvidersData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiFetch<ProvidersData>('/api/v1/admin/providers')
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((failure) => {
        if (cancelled || onError(failure)) return;
        setError((failure as ApiError).message);
      });
    return () => {
      cancelled = true;
    };
  }, [onError]);

  if (error) return <ErrorNote message={error} />;
  if (!data) return <p className="text-sm text-muted">Loading…</p>;

  return (
    <div className="grid gap-4">
      <div className="card overflow-x-auto">
        <h2 className="p-4 pb-0 text-sm font-semibold">Configuration</h2>
        <table className="mt-3 w-full text-left text-sm">
          <thead className="border-b border-border text-xs uppercase text-muted">
            <tr>
              <th className="p-3">Kind</th>
              <th className="p-3">Name</th>
              <th className="p-3">Enabled</th>
              <th className="p-3">Priority</th>
              <th className="p-3">Health</th>
              <th className="p-3">Daily cap</th>
              <th className="p-3">Key</th>
            </tr>
          </thead>
          <tbody>
            {data.configuration.map((row) => (
              <tr key={`${row.kind}-${row.name}`} className="border-b border-border/50 last:border-0">
                <td className="p-3">{row.kind}</td>
                <td className="p-3">{row.name}</td>
                <td className="p-3">{row.enabled ? 'yes' : 'no'}</td>
                <td className="p-3">{row.priority}</td>
                <td className="p-3">
                  <span className={row.health_status === 'unavailable' ? 'text-danger' : ''}>
                    {row.health_status ?? '—'}
                  </span>
                  {row.health_detail && (
                    <span className="block text-xs text-muted">{row.health_detail}</span>
                  )}
                </td>
                <td className="p-3">
                  {row.daily_cost_limit_usd === null ? '—' : `$${row.daily_cost_limit_usd}`}
                </td>
                <td className="p-3">{row.has_secrets ? 'set' : 'none'}</td>
              </tr>
            ))}
            {data.configuration.length === 0 && (
              <tr>
                <td className="p-4 text-muted" colSpan={7}>
                  No provider rows configured — the priority lists in `.env` are in force.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="card overflow-x-auto">
        <h2 className="p-4 pb-0 text-sm font-semibold">Usage, last 24h</h2>
        <table className="mt-3 w-full text-left text-sm">
          <thead className="border-b border-border text-xs uppercase text-muted">
            <tr>
              <th className="p-3">Provider</th>
              <th className="p-3">Calls</th>
              <th className="p-3">Cost</th>
              <th className="p-3">Avg latency</th>
              <th className="p-3">Success</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(data.usage_24h).map(([provider, stats]) => (
              <tr key={provider} className="border-b border-border/50 last:border-0">
                <td className="p-3">{provider}</td>
                <td className="p-3">{stats.calls}</td>
                <td className="p-3">${stats.cost_usd.toFixed(4)}</td>
                <td className="p-3">{stats.avg_latency_ms}ms</td>
                <td className="p-3">
                  <span className={stats.success_rate < 0.9 ? 'text-danger' : ''}>
                    {(stats.success_rate * 100).toFixed(1)}%
                  </span>
                </td>
              </tr>
            ))}
            {Object.keys(data.usage_24h).length === 0 && (
              <tr>
                <td className="p-4 text-muted" colSpan={5}>
                  No provider calls in the last 24 hours.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="card p-5">
        <h2 className="text-sm font-semibold">Live health</h2>
        <pre className="mt-3 overflow-x-auto rounded-lg bg-raised p-3 text-xs">
          {JSON.stringify(data.health, null, 2)}
        </pre>
      </div>
    </div>
  );
}
