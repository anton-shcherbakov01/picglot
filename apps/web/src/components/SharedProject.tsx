'use client';

import { useCallback, useEffect, useState } from 'react';

import { ApiError, apiFetch } from '@/lib/api';

interface ShareView {
  project: {
    name: string;
    tool: string;
    source_language: string | null;
    target_language: string | null;
    page_count: number;
    created_at: string;
  };
  owner: string | null;
  permission: string;
  can_download: boolean;
  watermark: boolean;
  pages: { page_number: number; width: number; height: number; image_url: string | null }[];
  expires_at: string | null;
}

export function SharedProject({ token }: { token: string }) {
  const [view, setView] = useState<ShareView | null>(null);
  const [password, setPassword] = useState('');
  const [needsPassword, setNeedsPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);

  const load = useCallback(
    async (secret?: string) => {
      setBusy(true);
      setError(null);
      try {
        const query = secret ? `?password=${encodeURIComponent(secret)}` : '';
        setView(await apiFetch<ShareView>(`/api/v1/share/${token}${query}`));
        setNeedsPassword(false);
      } catch (failure) {
        const apiError = failure as ApiError;
        if (apiError.code === 'unauthenticated' || apiError.code === 'invalid_credentials') {
          setNeedsPassword(true);
          if (apiError.code === 'invalid_credentials') setError('Incorrect password.');
        } else if (apiError.code === 'token_expired') {
          setError('This link has expired.');
        } else if (apiError.code === 'not_found') {
          setError('This link is not valid or has been revoked.');
        } else {
          setError(apiError.message);
        }
      } finally {
        setBusy(false);
      }
    },
    [token],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const download = async () => {
    try {
      const result = await apiFetch<{ url: string }>(
        `/api/v1/share/${token}/download${password ? `?password=${encodeURIComponent(password)}` : ''}`,
      );
      window.location.href = result.url;
    } catch (failure) {
      setError((failure as ApiError).message);
    }
  };

  if (needsPassword) {
    return (
      <form
        className="card mx-auto max-w-sm p-6"
        onSubmit={(event) => {
          event.preventDefault();
          void load(password);
        }}
      >
        <h1 className="text-lg font-semibold">This link is password protected</h1>
        {error && (
          <p role="alert" className="mt-3 text-sm text-danger">
            {error}
          </p>
        )}
        <label className="label mt-4" htmlFor="share-password">
          Password
        </label>
        <input
          id="share-password"
          type="password"
          className="input"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
        />
        <button type="submit" className="btn-primary mt-4 w-full">
          Open
        </button>
      </form>
    );
  }

  if (busy && !view) return <div className="skeleton h-96 rounded-card" />;

  if (error || !view) {
    return (
      <div className="card p-10 text-center">
        <h1 className="text-xl font-semibold">Not available</h1>
        <p className="mt-2 text-muted">{error ?? 'This link cannot be opened.'}</p>
      </div>
    );
  }

  return (
    <>
      <header className="mb-6">
        <h1 className="text-2xl font-bold">{view.project.name}</h1>
        <p className="mt-1 text-sm text-muted">
          {view.project.page_count} page(s)
          {view.owner ? ` · shared by ${view.owner}` : ''}
          {view.expires_at
            ? ` · link expires ${new Date(view.expires_at).toLocaleDateString()}`
            : ''}
        </p>
      </header>

      <div className="grid gap-4">
        {view.pages.map((page) =>
          page.image_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              key={page.page_number}
              src={page.image_url}
              alt={`Page ${page.page_number}`}
              className="w-full rounded-card border border-border"
            />
          ) : (
            <div key={page.page_number} className="skeleton h-64 rounded-card" />
          ),
        )}
      </div>

      {view.can_download && (
        <button type="button" className="btn-primary mt-6" onClick={() => void download()}>
          Download
        </button>
      )}

      <p className="mt-8 text-center text-xs text-muted">
        Shared with LingoImage AI · this page is not indexed
      </p>
    </>
  );
}
