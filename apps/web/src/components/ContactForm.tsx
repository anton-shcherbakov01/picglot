'use client';

import { useState } from 'react';

import { ApiError, apiFetch } from '@/lib/api';
import type { Locale } from '@/lib/i18n';

const CATEGORIES = ['general', 'billing', 'technical', 'abuse', 'privacy', 'partnership'] as const;

export function ContactForm({ locale }: { locale: Locale }) {
  const ru = locale === 'ru';
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      const body = new FormData();
      body.set(
        'payload',
        JSON.stringify({
          email: form.get('email'),
          name: form.get('name') || null,
          category: form.get('category'),
          subject: form.get('subject'),
          message: form.get('message'),
          project_id: form.get('project_id') || null,
          // Honeypot: a real person never fills this in.
          website: form.get('website') || '',
        }),
      );
      for (const file of form.getAll('attachments')) {
        if (file instanceof File && file.size > 0) body.append('attachments', file);
      }
      await apiFetch('/api/v1/contact', { method: 'POST', body });
      setSent(true);
    } catch (failure) {
      setError((failure as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  if (sent) {
    return (
      <div className="card p-8 text-center">
        <h1 className="text-xl font-semibold">{ru ? 'Сообщение отправлено' : 'Message sent'}</h1>
        <p className="mt-2 text-muted">
          {ru
            ? 'Мы обычно отвечаем в течение одного рабочего дня.'
            : 'We usually reply within one business day.'}
        </p>
      </div>
    );
  }

  return (
    <>
      <h1 className="text-3xl font-bold">{ru ? 'Связаться с нами' : 'Contact us'}</h1>
      <p className="mt-3 text-muted">
        {ru
          ? 'Опишите проблему как можно конкретнее. Если ошибка произошла при обработке, укажите ID проекта — это сильно ускорит разбор.'
          : 'Describe the problem as specifically as you can. If something failed during processing, include the project ID — it makes the investigation much faster.'}
      </p>

      {error && (
        <p role="alert" className="mt-4 rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}

      <form onSubmit={submit} className="mt-8 grid gap-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor="email">
              {ru ? 'Электронная почта' : 'Email'}
            </label>
            <input id="email" name="email" type="email" className="input" required />
          </div>
          <div>
            <label className="label" htmlFor="name">
              {ru ? 'Имя' : 'Name'}
            </label>
            <input id="name" name="name" className="input" autoComplete="name" />
          </div>
        </div>

        <div>
          <label className="label" htmlFor="category">
            {ru ? 'Тема обращения' : 'Category'}
          </label>
          <select id="category" name="category" className="input" defaultValue="general">
            {CATEGORIES.map((category) => (
              <option key={category} value={category}>
                {category}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="label" htmlFor="subject">
            {ru ? 'Заголовок' : 'Subject'}
          </label>
          <input id="subject" name="subject" className="input" required minLength={3} />
        </div>

        <div>
          <label className="label" htmlFor="message">
            {ru ? 'Сообщение' : 'Message'}
          </label>
          <textarea id="message" name="message" className="input min-h-40" required minLength={10} />
        </div>

        <div>
          <label className="label" htmlFor="project_id">
            {ru ? 'ID проекта (необязательно)' : 'Project ID (optional)'}
          </label>
          <input id="project_id" name="project_id" className="input" placeholder="prj_…" />
        </div>

        <div>
          <label className="label" htmlFor="attachments">
            {ru ? 'Вложения (необязательно, до 3 файлов)' : 'Attachments (optional, up to 3 files)'}
          </label>
          <input
            id="attachments"
            name="attachments"
            type="file"
            accept="image/*,.pdf"
            multiple
            className="input"
          />
        </div>

        {/* Honeypot — visually and programmatically hidden from real users. */}
        <div aria-hidden className="hidden">
          <label htmlFor="website">Website</label>
          <input id="website" name="website" tabIndex={-1} autoComplete="off" />
        </div>

        <button type="submit" className="btn-primary justify-self-start" disabled={busy}>
          {busy ? '…' : ru ? 'Отправить' : 'Send'}
        </button>
      </form>
    </>
  );
}
