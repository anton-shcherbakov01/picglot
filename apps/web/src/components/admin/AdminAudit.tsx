'use client';

import { useCallback, useEffect, useState } from 'react';

import { ApiError, apiFetch } from '@/lib/api';
import type { Locale } from '@/lib/i18n';

import { ErrorNote, Timestamp } from './AdminPanel';

interface AuditRow {
  id: string;
  actor_user_id: string | null;
  actor_role: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  reason: string | null;
  created_at: string;
  data: Record<string, unknown>;
}

export function AdminAudit({
  locale,
  onError,
}: {
  locale: Locale;
  onError: (failure: unknown) => boolean;
}) {
  const [action, setAction] = useState('');
  const [rows, setRows] = useState<AuditRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const query = new URLSearchParams({ limit: '50' });
      if (action) query.set('action', action);
      const result = await apiFetch<{ items: AuditRow[] }>(`/api/v1/admin/audit?${query}`);
      setRows(result.items);
      setError(null);
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    }
  }, [action, onError]);

  useEffect(() => {
    const timer = setTimeout(() => void load(), action ? 300 : 0);
    return () => clearTimeout(timer);
  }, [load, action]);

  if (error) return <ErrorNote message={error} />;

  return (
    <div className="grid gap-4">
      <input
        className="input max-w-sm"
        placeholder="Filter by action, e.g. credits.adjust"
        value={action}
        onChange={(event) => setAction(event.target.value)}
        aria-label="Filter audit log by action"
      />

      <div className="card overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-border text-xs uppercase text-muted">
            <tr>
              <th className="p-3">When</th>
              <th className="p-3">Actor</th>
              <th className="p-3">Action</th>
              <th className="p-3">Target</th>
              <th className="p-3">Reason</th>
            </tr>
          </thead>
          <tbody>
            {rows?.map((row) => (
              <tr key={row.id} className="border-b border-border/50 last:border-0 align-top">
                <td className="p-3">
                  <Timestamp iso={row.created_at} locale={locale} />
                </td>
                <td className="p-3">
                  {row.actor_role ?? '—'}
                  {row.actor_user_id && (
                    <span className="block text-xs text-muted">{row.actor_user_id}</span>
                  )}
                </td>
                <td className="p-3">{row.action}</td>
                <td className="p-3">
                  {row.target_type ?? '—'}
                  {row.target_id && <span className="block text-xs text-muted">{row.target_id}</span>}
                </td>
                <td className="p-3">
                  {row.reason ?? '—'}
                  {Object.keys(row.data).length > 0 && (
                    <code className="mt-1 block text-xs text-muted">{JSON.stringify(row.data)}</code>
                  )}
                </td>
              </tr>
            ))}
            {rows?.length === 0 && (
              <tr>
                <td className="p-4 text-muted" colSpan={5}>
                  No audit entries match.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
