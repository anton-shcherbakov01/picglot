import type { Metadata } from 'next';
import { notFound } from 'next/navigation';

import { serverFetch } from '@/lib/api';
import { absoluteUrl, isLocale, localePath } from '@/lib/i18n';

interface StatusResponse {
  overall: string;
  components: { key: string; state: string; detail: string }[];
  incidents: {
    id: string;
    component: string;
    title: string;
    severity: string;
    started_at: string;
    scheduled: boolean;
  }[];
  checked_at: string;
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  return {
    title: locale === 'ru' ? 'Статус сервиса' : 'Service status',
    alternates: { canonical: absoluteUrl(localePath(locale, 'status')) },
  };
}

// Status must reflect reality, so it is never cached for long.
export const revalidate = 30;

const TONE: Record<string, string> = {
  operational: 'text-ok',
  degraded: 'text-warn',
  maintenance: 'text-warn',
  down: 'text-danger',
};

export default async function StatusPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const ru = locale === 'ru';

  const status = await serverFetch<StatusResponse>('/api/v1/status', { revalidate: 30 });

  if (!status) {
    return (
      <div className="container-page py-16 text-center">
        <h1 className="text-2xl font-semibold">{ru ? 'Статус сервиса' : 'Service status'}</h1>
        <p className="mt-3 text-danger">
          {ru
            ? 'Не удалось получить статус — API недоступен.'
            : 'Status is unavailable — the API could not be reached.'}
        </p>
      </div>
    );
  }

  const LABELS: Record<string, { en: string; ru: string }> = {
    web: { en: 'Website', ru: 'Сайт' },
    api: { en: 'API', ru: 'API' },
    processing: { en: 'Processing', ru: 'Обработка' },
    translation: { en: 'Translation', ru: 'Перевод' },
    storage: { en: 'File storage', ru: 'Хранилище файлов' },
    payments: { en: 'Payments', ru: 'Платежи' },
  };

  return (
    <div className="container-page max-w-3xl py-12">
      <h1 className="text-3xl font-bold">{ru ? 'Статус сервиса' : 'Service status'}</h1>
      <p className={`mt-3 text-lg font-semibold ${TONE[status.overall] ?? ''}`}>
        {status.overall === 'operational'
          ? ru
            ? 'Все системы работают'
            : 'All systems operational'
          : status.overall === 'maintenance'
            ? ru
              ? 'Технические работы'
              : 'Under maintenance'
            : status.overall === 'degraded'
              ? ru
                ? 'Частичная деградация'
                : 'Partially degraded'
              : ru
                ? 'Сбой'
                : 'Outage'}
      </p>

      <ul className="card mt-8 divide-y divide-border">
        {status.components.map((component) => (
          <li key={component.key} className="flex items-center gap-3 px-4 py-3">
            <span className={TONE[component.state] ?? ''} aria-hidden>
              ●
            </span>
            <span className="flex-1">
              {ru ? LABELS[component.key]?.ru : LABELS[component.key]?.en}
            </span>
            <span className={`text-sm ${TONE[component.state] ?? ''}`}>
              {component.state}
            </span>
            {component.detail && (
              <span className="text-xs text-muted">{component.detail}</span>
            )}
          </li>
        ))}
      </ul>

      <h2 className="mt-10 text-xl font-semibold">
        {ru ? 'Инциденты' : 'Incidents'}
      </h2>
      {status.incidents.length === 0 ? (
        <p className="mt-3 text-muted">
          {ru ? 'Открытых инцидентов нет.' : 'No open incidents.'}
        </p>
      ) : (
        <ul className="mt-3 grid gap-3">
          {status.incidents.map((incident) => (
            <li key={incident.id} className="card p-4">
              <p className="font-medium">{incident.title}</p>
              <p className="mt-1 text-xs text-muted">
                {incident.component} · {incident.severity} ·{' '}
                {new Date(incident.started_at).toLocaleString(locale)}
              </p>
            </li>
          ))}
        </ul>
      )}

      <p className="mt-8 text-xs text-muted">
        {ru ? 'Проверено' : 'Checked'}: {new Date(status.checked_at).toLocaleString(locale)}
      </p>
    </div>
  );
}
