'use client';

import { useCallback, useEffect, useState } from 'react';

import { ApiError, apiFetch } from '@/lib/api';
import { formatNumber } from '@/lib/format';
import type { Locale } from '@/lib/i18n';

import { ErrorNote, Timestamp, useReasonPrompt } from './AdminPanel';

interface JobRow {
  id: string;
  type: string;
  status: string;
  error_code?: string | null;
  created_at: string;
  pages_total?: number | null;
  pages_completed?: number | null;
}

interface JobList {
  items: JobRow[];
  total: number;
  limit: number;
  offset: number;
}

interface JobDetail {
  job: JobRow;
  timeline: { stage: string; progress: number; at: string; data: Record<string, unknown> }[];
  provider_calls: {
    kind: string;
    provider: string;
    model: string | null;
    units: number;
    unit_kind: string;
    cost_usd: number;
    latency_ms: number | null;
    success: boolean;
  }[];
  internal_error_reference: string | null;
}

const STATUSES = [
  '',
  'queued',
  'running',
  'completed',
  'partially_completed',
  'failed',
  'cancelled',
];

export function AdminJobs({
  locale,
  onError,
}: {
  locale: Locale;
  onError: (failure: unknown) => boolean;
}) {
  const [status, setStatus] = useState('');
  const [errorCode, setErrorCode] = useState('');
  const [list, setList] = useState<JobList | null>(null);
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const askReason = useReasonPrompt();

  const load = useCallback(async () => {
    try {
      const query = new URLSearchParams({ limit: '25' });
      if (status) query.set('job_status', status);
      if (errorCode) query.set('error_code', errorCode);
      setList(await apiFetch<JobList>(`/api/v1/admin/jobs?${query}`));
      setError(null);
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    }
  }, [status, errorCode, onError]);

  useEffect(() => {
    const timer = setTimeout(() => void load(), errorCode ? 300 : 0);
    return () => clearTimeout(timer);
  }, [load, errorCode]);

  const openDetail = async (id: string) => {
    setDetail(null);
    try {
      setDetail(await apiFetch<JobDetail>(`/api/v1/admin/jobs/${id}`));
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    }
  };

  const retry = async (id: string) => {
    try {
      await apiFetch(`/api/v1/admin/jobs/${id}/retry`, { method: 'POST' });
      await load();
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    }
  };

  const refund = async (id: string) => {
    const reason = askReason('refund job');
    if (!reason) return;
    const raw = window.prompt('Amount in credits (leave blank to refund the full charge):', '');
    const amount = raw && raw.trim() ? Number(raw) : null;
    if (amount !== null && (!Number.isInteger(amount) || amount <= 0)) {
      window.alert('Enter a positive whole number, or leave blank.');
      return;
    }
    try {
      await apiFetch(`/api/v1/admin/jobs/${id}/refund`, {
        method: 'POST',
        json: amount === null ? { reason } : { reason, amount },
      });
      await load();
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    }
  };

  return (
    <div className="grid gap-4">
      {error && <ErrorNote message={error} />}

      <div className="flex flex-wrap gap-3">
        <div>
          <label className="label" htmlFor="job-status">
            Status
          </label>
          <select
            id="job-status"
            className="input w-auto"
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            {STATUSES.map((value) => (
              <option key={value} value={value}>
                {value || 'any'}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="job-error">
            Error code
          </label>
          <input
            id="job-error"
            className="input"
            placeholder="provider_timeout"
            value={errorCode}
            onChange={(event) => setErrorCode(event.target.value)}
          />
        </div>
      </div>

      <div className="card overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-border text-xs uppercase text-muted">
            <tr>
              <th className="p-3">Type</th>
              <th className="p-3">Status</th>
              <th className="p-3">Error</th>
              <th className="p-3">Pages</th>
              <th className="p-3">Created</th>
              <th className="p-3" />
            </tr>
          </thead>
          <tbody>
            {list?.items.map((row) => (
              <tr key={row.id} className="border-b border-border/50 last:border-0">
                <td className="p-3">{row.type}</td>
                <td className="p-3">
                  <span className={row.status === 'failed' ? 'text-danger' : ''}>{row.status}</span>
                </td>
                <td className="p-3">{row.error_code ?? '—'}</td>
                <td className="p-3">
                  {row.pages_completed ?? 0}/{row.pages_total ?? '?'}
                </td>
                <td className="p-3">
                  <Timestamp iso={row.created_at} locale={locale} />
                </td>
                <td className="p-3 text-right">
                  <div className="flex justify-end gap-1">
                    <button type="button" className="btn-ghost text-xs" onClick={() => void openDetail(row.id)}>
                      Detail
                    </button>
                    <button type="button" className="btn-ghost text-xs" onClick={() => void retry(row.id)}>
                      Retry
                    </button>
                    <button type="button" className="btn-ghost text-xs" onClick={() => void refund(row.id)}>
                      Refund
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {list?.items.length === 0 && (
              <tr>
                <td className="p-4 text-muted" colSpan={6}>
                  No jobs match.
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

      {detail && (
        <div className="card p-5">
          <div className="flex items-start justify-between gap-4">
            <h2 className="text-sm font-semibold">Job {detail.job.id}</h2>
            <button type="button" className="btn-ghost text-xs" onClick={() => setDetail(null)}>
              Close
            </button>
          </div>

          {detail.internal_error_reference && (
            <p className="mt-3 text-xs text-muted">
              Internal error reference: <code>{detail.internal_error_reference}</code>
            </p>
          )}

          <h3 className="mt-4 text-xs uppercase text-muted">Timeline</h3>
          <ul className="mt-2 grid gap-1 text-sm">
            {detail.timeline.map((event, index) => (
              <li key={`${event.stage}-${index}`} className="flex justify-between gap-3">
                <span>
                  {event.stage} · {Math.round(event.progress * 100)}%
                </span>
                <Timestamp iso={event.at} locale={locale} />
              </li>
            ))}
            {detail.timeline.length === 0 && <li className="text-muted">No events.</li>}
          </ul>

          <h3 className="mt-4 text-xs uppercase text-muted">Provider calls</h3>
          <ul className="mt-2 grid gap-1 text-sm">
            {detail.provider_calls.map((call, index) => (
              <li key={index} className="flex flex-wrap justify-between gap-3">
                <span>
                  {call.kind} · {call.provider}
                  {call.model ? ` (${call.model})` : ''} · {call.units} {call.unit_kind}
                </span>
                <span className={call.success ? 'text-muted' : 'text-danger'}>
                  ${call.cost_usd.toFixed(4)}
                  {call.latency_ms !== null ? ` · ${call.latency_ms}ms` : ''}
                  {call.success ? '' : ' · failed'}
                </span>
              </li>
            ))}
            {detail.provider_calls.length === 0 && <li className="text-muted">None recorded.</li>}
          </ul>
        </div>
      )}
    </div>
  );
}
