import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';

import { API_URL } from '@/lib/api';
import { absoluteUrl, alternates, isLocale, localePath } from '@/lib/i18n';

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const { languages } = alternates('/api');
  return {
    title: locale === 'ru' ? 'API для разработчиков' : 'Developer API',
    description:
      locale === 'ru'
        ? 'REST API для распознавания, перевода и конвертации изображений и документов.'
        : 'REST API for recognising, translating and converting images and documents.',
    alternates: { canonical: absoluteUrl(localePath(locale, 'api')), languages },
  };
}

const CURL = `curl -X POST "$API/api/v1/process" \\
  -H "Authorization: Bearer $LINGOIMAGE_API_KEY" \\
  -H "Idempotency-Key: $(uuidgen)" \\
  -F "file=@invoice.pdf" \\
  -F 'options={"tool":"pdf-translator","target_language":"en","export_formats":["pdf_searchable"]}'`;

const JS = `const form = new FormData();
form.append('file', file);
form.append('options', JSON.stringify({
  tool: 'image-translator',
  target_language: 'ru',
  export_formats: ['png'],
}));

const job = await fetch(\`\${API}/api/v1/process\`, {
  method: 'POST',
  headers: {
    Authorization: \`Bearer \${process.env.LINGOIMAGE_API_KEY}\`,
    'Idempotency-Key': crypto.randomUUID(),
  },
  body: form,
}).then((response) => response.json());

// Follow progress until the job reaches a terminal state.
const events = new EventSource(\`\${API}/api/v1/jobs/\${job.id}/events\`);
events.addEventListener('done', async () => {
  events.close();
  const result = await fetch(\`\${API}/api/v1/jobs/\${job.id}\`).then((r) => r.json());
  console.log(result.output.exports);
});`;

const PY = `import os, time, requests

API = os.environ["LINGOIMAGE_API_URL"]
headers = {"Authorization": f"Bearer {os.environ['LINGOIMAGE_API_KEY']}"}

with open("receipt.jpg", "rb") as handle:
    job = requests.post(
        f"{API}/api/v1/process",
        headers=headers,
        files={"file": handle},
        data={"options": '{"tool":"receipt-scanner","export_formats":["json"]}'},
    ).json()

while True:
    status = requests.get(f"{API}/api/v1/jobs/{job['id']}", headers=headers).json()
    if status["status"] in {"completed", "partially_completed", "failed"}:
        break
    time.sleep(1.5)

print(status["output"])`;

const STATUSES = [
  'created', 'uploading', 'queued', 'preprocessing', 'detecting', 'recognizing',
  'translating', 'inpainting', 'rendering', 'exporting', 'completed',
  'partially_completed', 'failed', 'cancelled',
];

export default async function ApiPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const ru = locale === 'ru';

  return (
    <div className="container-page max-w-4xl py-12">
      <h1 className="text-3xl font-bold">{ru ? 'API для разработчиков' : 'Developer API'}</h1>
      <p className="mt-3 text-muted">
        {ru
          ? 'Тот же движок, что и в интерфейсе: загрузка, распознавание, перевод и экспорт через REST.'
          : 'The same engine the web app uses: upload, recognise, translate and export over REST.'}
      </p>

      <div className="mt-6 flex flex-wrap gap-3">
        <a href={`${API_URL}/docs`} className="btn-primary" target="_blank" rel="noreferrer">
          {ru ? 'Открыть Swagger UI' : 'Open Swagger UI'}
        </a>
        <a href={`${API_URL}/openapi.json`} className="btn-secondary" target="_blank" rel="noreferrer">
          OpenAPI 3
        </a>
        <Link href={localePath(locale, 'app')} className="btn-secondary">
          {ru ? 'Создать API-ключ' : 'Create an API key'}
        </Link>
      </div>

      <section className="mt-12">
        <h2 className="text-xl font-semibold">{ru ? 'Аутентификация' : 'Authentication'}</h2>
        <p className="mt-2 text-muted">
          {ru
            ? 'Отправляйте ключ в заголовке Authorization. Ключ показывается один раз при создании и хранится у нас только в виде хеша.'
            : 'Send the key in the Authorization header. It is shown once at creation and stored only as a hash.'}
        </p>
        <pre className="scroll-x mt-3 rounded-card border border-border bg-raised p-4 text-xs">
          <code>Authorization: Bearer lik_live_…</code>
        </pre>
      </section>

      <section className="mt-10">
        <h2 className="text-xl font-semibold">{ru ? 'Идемпотентность' : 'Idempotency'}</h2>
        <p className="mt-2 text-muted">
          {ru
            ? 'Передайте заголовок Idempotency-Key при создании задания. Повтор того же запроса вернёт исходное задание и не спишет кредиты дважды.'
            : 'Send an Idempotency-Key header when creating a job. Repeating the request returns the original job and never charges twice.'}
        </p>
      </section>

      <section className="mt-10">
        <h2 className="text-xl font-semibold">curl</h2>
        <pre className="scroll-x mt-3 rounded-card border border-border bg-raised p-4 text-xs">
          <code>{CURL}</code>
        </pre>
      </section>

      <section className="mt-10">
        <h2 className="text-xl font-semibold">JavaScript</h2>
        <pre className="scroll-x mt-3 rounded-card border border-border bg-raised p-4 text-xs">
          <code>{JS}</code>
        </pre>
      </section>

      <section className="mt-10">
        <h2 className="text-xl font-semibold">Python</h2>
        <pre className="scroll-x mt-3 rounded-card border border-border bg-raised p-4 text-xs">
          <code>{PY}</code>
        </pre>
      </section>

      <section className="mt-10">
        <h2 className="text-xl font-semibold">{ru ? 'Статусы задания' : 'Job statuses'}</h2>
        <div className="mt-3 flex flex-wrap gap-2">
          {STATUSES.map((status) => (
            <code key={status} className="chip font-mono">
              {status}
            </code>
          ))}
        </div>
      </section>

      <section className="mt-10">
        <h2 className="text-xl font-semibold">Webhooks</h2>
        <p className="mt-2 text-muted">
          {ru
            ? 'Каждая доставка подписана HMAC-SHA256 над «timestamp.body». Проверяйте подпись и отклоняйте старые метки времени.'
            : 'Every delivery is signed with HMAC-SHA256 over "timestamp.body". Verify the signature and reject stale timestamps.'}
        </p>
        <pre className="scroll-x mt-3 rounded-card border border-border bg-raised p-4 text-xs">
          <code>{`X-LingoImage-Signature: t=1735689600,v1=<hex>
X-LingoImage-Event: job.completed
X-LingoImage-Event-Id: evt_…
X-LingoImage-Delivery: dlv_…`}</code>
        </pre>
      </section>

      <section className="mt-10">
        <h2 className="text-xl font-semibold">{ru ? 'Ошибки' : 'Errors'}</h2>
        <p className="mt-2 text-muted">
          {ru
            ? 'Каждая ошибка возвращает стабильный машинночитаемый код. Переключайтесь по нему, а не по тексту сообщения.'
            : 'Every error returns a stable machine-readable code. Switch on the code, never on the message text.'}
        </p>
        <pre className="scroll-x mt-3 rounded-card border border-border bg-raised p-4 text-xs">
          <code>{`{
  "error": {
    "code": "page_limit_exceeded",
    "message": "The document has more pages than your plan allows.",
    "retryable": false,
    "details": { "pages": 420, "limit": 300, "plan": "pro" },
    "request_id": "req_01J…"
  }
}`}</code>
        </pre>
      </section>
    </div>
  );
}
