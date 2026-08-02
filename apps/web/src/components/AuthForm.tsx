'use client';

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useState } from 'react';

import { track } from '@/lib/analytics';
import { ApiError, apiFetch } from '@/lib/api';
import { localePath, type Locale } from '@/lib/i18n';
import type { Messages } from '@/lib/messages';

type Action = 'sign-in' | 'sign-up' | 'forgot' | 'reset' | 'verify' | 'magic' | 'two-factor';

interface AuthResponse {
  user: { id: string; email: string } | null;
  requires_two_factor: boolean;
  pending_token: string | null;
  csrf_token: string | null;
}

export function AuthForm({
  locale,
  messages,
  action,
}: {
  locale: Locale;
  messages: Messages;
  action: Action;
}) {
  const router = useRouter();
  const search = useSearchParams();
  const token = search.get('token') ?? '';

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [code, setCode] = useState('');
  const [pendingToken, setPendingToken] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // One-time links land here with ?token=… and complete without any typing.
  useEffect(() => {
    if (!token) return;
    if (action === 'verify') {
      void run(async () => {
        await apiFetch('/api/v1/auth/verify-email', { method: 'POST', json: { token } });
        setNotice('Email confirmed. You can sign in now.');
      });
    }
    if (action === 'magic') {
      void run(async () => {
        const result = await apiFetch<AuthResponse>('/api/v1/auth/magic-link/verify', {
          method: 'POST',
          json: { token },
        });
        finish(result);
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, action]);

  const describe = (failure: unknown): string => {
    const apiError = failure as ApiError;
    return (
      (messages.errors as Record<string, string>)[apiError.code] ??
      apiError.message ??
      messages.errors.internal_error
    );
  };

  async function run(work: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (failure) {
      setError(describe(failure));
    } finally {
      setBusy(false);
    }
  }

  function finish(result: AuthResponse) {
    if (result.requires_two_factor) {
      setPendingToken(result.pending_token);
      return;
    }
    router.push(localePath(locale, 'app'));
    router.refresh();
  }

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    void run(async () => {
      if (pendingToken) {
        finish(
          await apiFetch<AuthResponse>('/api/v1/auth/2fa/verify', {
            method: 'POST',
            json: { code, pending_token: pendingToken },
          }),
        );
        return;
      }
      switch (action) {
        case 'sign-up': {
          track('signup_started');
          const result = await apiFetch<AuthResponse>('/api/v1/auth/register', {
            method: 'POST',
            json: { email, password, name: name || null, locale, accept_terms: true },
          });
          track('signup_completed');
          finish(result);
          break;
        }
        case 'sign-in':
          finish(
            await apiFetch<AuthResponse>('/api/v1/auth/login', {
              method: 'POST',
              json: { email, password },
            }),
          );
          break;
        case 'forgot':
          await apiFetch('/api/v1/auth/forgot-password', {
            method: 'POST',
            json: { email },
          });
          setNotice(messages.auth.magicLinkSent);
          break;
        case 'magic':
          await apiFetch('/api/v1/auth/magic-link', { method: 'POST', json: { email } });
          setNotice(messages.auth.magicLinkSent);
          break;
        case 'reset':
          await apiFetch('/api/v1/auth/reset-password', {
            method: 'POST',
            json: { token, password },
          });
          setNotice('Password updated. You can sign in now.');
          break;
        default:
          break;
      }
    });
  };

  const title = pendingToken
    ? messages.auth.twoFactorTitle
    : action === 'sign-up'
      ? messages.auth.signUpTitle
      : action === 'forgot' || action === 'reset'
        ? messages.auth.resetTitle
        : action === 'verify'
          ? messages.auth.verifyTitle
          : messages.auth.signInTitle;

  const showEmail = !pendingToken && ['sign-in', 'sign-up', 'forgot', 'magic'].includes(action);
  const showPassword = !pendingToken && ['sign-in', 'sign-up', 'reset'].includes(action);

  return (
    <div className="card p-6 sm:p-8">
      <h1 className="text-xl font-semibold">{title}</h1>

      {error && (
        <p role="alert" className="mt-4 rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="mt-4 rounded-lg border border-ok/40 bg-ok/10 px-3 py-2 text-sm text-ok">
          {notice}
        </p>
      )}

      <form onSubmit={submit} className="mt-5 grid gap-4">
        {pendingToken && (
          <div>
            <label className="label" htmlFor="code">
              {messages.auth.twoFactorTitle}
            </label>
            <input
              id="code"
              className="input"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={code}
              onChange={(event) => setCode(event.target.value)}
              required
            />
            <p className="mt-1 text-xs text-muted">{messages.auth.twoFactorHint}</p>
          </div>
        )}

        {action === 'sign-up' && (
          <div>
            <label className="label" htmlFor="name">
              {messages.auth.name}{' '}
              <span className="font-normal text-muted">({messages.common.optional})</span>
            </label>
            <input
              id="name"
              className="input"
              autoComplete="name"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
        )}

        {showEmail && (
          <div>
            <label className="label" htmlFor="email">
              {messages.auth.email}
            </label>
            <input
              id="email"
              type="email"
              className="input"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </div>
        )}

        {showPassword && (
          <div>
            <label className="label" htmlFor="password">
              {messages.auth.password}
            </label>
            <input
              id="password"
              type="password"
              className="input"
              autoComplete={action === 'sign-in' ? 'current-password' : 'new-password'}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
              minLength={action === 'sign-in' ? undefined : 10}
              aria-describedby={action === 'sign-in' ? undefined : 'password-hint'}
            />
            {action !== 'sign-in' && (
              <p id="password-hint" className="mt-1 text-xs text-muted">
                {messages.auth.passwordHint}
              </p>
            )}
          </div>
        )}

        {(pendingToken || showEmail || showPassword) && (
          <button type="submit" className="btn-primary" disabled={busy}>
            {busy
              ? messages.common.loading
              : action === 'sign-up'
                ? messages.auth.submitSignUp
                : messages.auth.submitSignIn}
          </button>
        )}
      </form>

      {action === 'sign-up' && (
        <p className="mt-4 text-xs text-muted">{messages.auth.terms}</p>
      )}

      <div className="mt-6 grid gap-2 text-sm">
        {action === 'sign-in' && (
          <>
            <Link href={localePath(locale, 'auth/magic')} className="text-accent hover:underline">
              {messages.auth.magicLink}
            </Link>
            <Link href={localePath(locale, 'auth/forgot')} className="text-accent hover:underline">
              {messages.auth.forgot}
            </Link>
            <p className="text-muted">
              {messages.auth.noAccount}{' '}
              <Link href={localePath(locale, 'auth/sign-up')} className="text-accent hover:underline">
                {messages.nav.signUp}
              </Link>
            </p>
          </>
        )}
        {action !== 'sign-in' && (
          <p className="text-muted">
            {messages.auth.haveAccount}{' '}
            <Link href={localePath(locale, 'auth/sign-in')} className="text-accent hover:underline">
              {messages.nav.signIn}
            </Link>
          </p>
        )}
      </div>
    </div>
  );
}
