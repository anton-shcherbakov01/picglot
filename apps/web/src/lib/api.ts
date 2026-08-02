/**
 * API client.
 *
 * Two entry points: `serverFetch` runs during SSR and talks to the API over the
 * internal network; `apiFetch` runs in the browser, sends cookies and attaches
 * the CSRF token. Errors are normalised into `ApiError` so components can react
 * to a stable `code` instead of parsing messages.
 */

export const API_URL = (
  process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
).replace(/\/$/, '');

const INTERNAL_API_URL = (
  process.env.API_INTERNAL_URL ?? API_URL
).replace(/\/$/, '');

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly retryable: boolean;
  readonly details: Record<string, unknown>;
  readonly requestId?: string;

  constructor(init: {
    code: string;
    message: string;
    status: number;
    retryable?: boolean;
    details?: Record<string, unknown>;
    requestId?: string;
  }) {
    super(init.message);
    this.name = 'ApiError';
    this.code = init.code;
    this.status = init.status;
    this.retryable = init.retryable ?? false;
    this.details = init.details ?? {};
    this.requestId = init.requestId;
  }
}

function readCookie(name: string): string | undefined {
  if (typeof document === 'undefined') return undefined;
  return document.cookie
    .split('; ')
    .find((row) => row.startsWith(`${name}=`))
    ?.split('=')[1];
}

async function toError(response: Response): Promise<ApiError> {
  let payload: { error?: Record<string, unknown> } = {};
  try {
    payload = await response.json();
  } catch {
    /* non-JSON error body (proxy, gateway) */
  }
  const error = payload.error ?? {};
  return new ApiError({
    code: String(error.code ?? 'internal_error'),
    message: String(error.message ?? 'Something went wrong.'),
    status: response.status,
    retryable: Boolean(error.retryable),
    details: (error.details as Record<string, unknown>) ?? {},
    requestId: error.request_id as string | undefined,
  });
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit & { json?: unknown } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.json !== undefined) headers.set('Content-Type', 'application/json');

  const csrf = readCookie('lingo_csrf');
  if (csrf && init.method && init.method !== 'GET') headers.set('X-CSRF-Token', csrf);

  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers,
    credentials: 'include',
    body: init.json !== undefined ? JSON.stringify(init.json) : init.body,
  });

  if (!response.ok) throw await toError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** Server-side fetch used by React Server Components. */
export async function serverFetch<T>(
  path: string,
  init: RequestInit & { revalidate?: number } = {},
): Promise<T | null> {
  try {
    const response = await fetch(`${INTERNAL_API_URL}${path}`, {
      ...init,
      next: { revalidate: init.revalidate ?? 300 },
    });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    // A cold API must not take the marketing pages down with it.
    return null;
  }
}

/** Upload a file and start processing. Reports progress from the XHR. */
export function uploadAndProcess(
  file: File,
  options: Record<string, unknown>,
  handlers: {
    onProgress?: (fraction: number) => void;
    signal?: AbortSignal;
  } = {},
): Promise<JobResponse> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append('file', file);
    form.append('options', JSON.stringify(options));

    const request = new XMLHttpRequest();
    request.open('POST', `${API_URL}/api/v1/process`);
    request.withCredentials = true;
    const csrf = readCookie('lingo_csrf');
    if (csrf) request.setRequestHeader('X-CSRF-Token', csrf);

    request.upload.onprogress = (event) => {
      if (event.lengthComputable) handlers.onProgress?.(event.loaded / event.total);
    };
    request.onload = () => {
      let payload: Record<string, unknown> = {};
      try {
        payload = JSON.parse(request.responseText);
      } catch {
        /* ignore */
      }
      if (request.status >= 200 && request.status < 300) {
        resolve(payload as unknown as JobResponse);
      } else {
        const error = (payload.error ?? {}) as Record<string, unknown>;
        reject(
          new ApiError({
            code: String(error.code ?? 'internal_error'),
            message: String(error.message ?? 'Upload failed.'),
            status: request.status,
            retryable: Boolean(error.retryable),
            details: (error.details as Record<string, unknown>) ?? {},
            requestId: error.request_id as string | undefined,
          }),
        );
      }
    };
    request.onerror = () =>
      reject(new ApiError({ code: 'network_error', message: 'Network error.', status: 0 }));
    request.onabort = () =>
      reject(new ApiError({ code: 'cancelled', message: 'Upload cancelled.', status: 0 }));

    handlers.signal?.addEventListener('abort', () => request.abort());
    request.send(form);
  });
}

/**
 * Follow a job to completion.
 *
 * Prefers Server-Sent Events and falls back to polling when EventSource is
 * unavailable or the stream drops — the caller sees one consistent callback.
 */
export function followJob(
  jobId: string,
  handlers: {
    onProgress: (job: Partial<JobResponse> & { progress: number; status: string }) => void;
    onDone: (job: JobResponse) => void;
    onError: (error: ApiError) => void;
  },
): () => void {
  let stopped = false;
  let source: EventSource | null = null;
  let pollTimer: ReturnType<typeof setTimeout> | null = null;

  const finish = async () => {
    if (stopped) return;
    try {
      const job = await apiFetch<JobResponse>(`/api/v1/jobs/${jobId}`);
      stop();
      if (job.status === 'failed') {
        handlers.onError(
          new ApiError({
            code: job.error?.code ?? 'internal_error',
            message: job.error?.message ?? 'Processing failed.',
            status: 500,
            retryable: job.error?.retryable ?? false,
          }),
        );
      } else {
        handlers.onDone(job);
      }
    } catch (error) {
      stop();
      handlers.onError(error as ApiError);
    }
  };

  const poll = async () => {
    if (stopped) return;
    try {
      const job = await apiFetch<JobResponse>(`/api/v1/jobs/${jobId}`);
      handlers.onProgress(job);
      if (TERMINAL.has(job.status)) return finish();
    } catch (error) {
      stop();
      return handlers.onError(error as ApiError);
    }
    pollTimer = setTimeout(poll, 1500);
  };

  const stop = () => {
    stopped = true;
    source?.close();
    if (pollTimer) clearTimeout(pollTimer);
  };

  if (typeof EventSource !== 'undefined') {
    source = new EventSource(`${API_URL}/api/v1/jobs/${jobId}/events`, {
      withCredentials: true,
    });
    source.addEventListener('progress', (event) => {
      try {
        handlers.onProgress(JSON.parse((event as MessageEvent).data));
      } catch {
        /* keep streaming */
      }
    });
    source.addEventListener('done', () => void finish());
    source.onerror = () => {
      source?.close();
      source = null;
      if (!stopped) void poll();
    };
  } else {
    void poll();
  }

  return stop;
}

const TERMINAL = new Set([
  'completed',
  'partially_completed',
  'failed',
  'cancelled',
]);

// --------------------------------------------------------------------------
// Shared response shapes (kept in step with apps/api/lingoimage/schemas.py)
// --------------------------------------------------------------------------
export interface JobResponse {
  id: string;
  project_id: string | null;
  type: string;
  status: string;
  stage: string | null;
  progress: number;
  pages_total: number;
  pages_completed: number;
  pages_failed: number;
  credits_charged: number;
  credits_refunded: number;
  cost_estimate: { total_credits?: number; breakdown?: unknown[] };
  error: { code: string; message: string; retryable: boolean; reference?: string } | null;
  output: Record<string, unknown>;
}

export interface RegionResponse {
  id: string;
  region_type: string;
  bounding_box: { x: number; y: number; width: number; height: number };
  polygon: number[][];
  rotation: number;
  reading_order: number;
  detected_language: string | null;
  source_text: string;
  normalized_text: string | null;
  translated_text: string | null;
  confidence: number | null;
  low_confidence_spans: { start: number; end: number; reason: string }[];
  style: Record<string, unknown>;
  skip_translation: boolean;
  version: number;
  translation: { provider?: string | null; source?: string | null; alternatives: string[] } | null;
}

export interface PageResponse {
  id: string;
  page_number: number;
  width: number;
  height: number;
  status: string;
  detected_language: string | null;
  ocr_confidence: number | null;
  preview_url: string | null;
  thumbnail_url: string | null;
  rendered_url: string | null;
  original_url: string | null;
  regions: RegionResponse[];
  tables: TableResponse[];
}

export interface TableResponse {
  id: string;
  rows: number;
  cols: number;
  has_header: boolean;
  structure_ambiguous: boolean;
  confidence: number | null;
  cells: {
    row: number;
    col: number;
    text: string;
    value_type: string;
    is_header: boolean;
    confidence: number | null;
  }[];
}

export interface ProjectResponse {
  id: string;
  name: string;
  tool_type: string;
  status: string;
  source_language: string | null;
  target_language: string | null;
  page_count: number;
  quality_score: number | null;
  quality_band: string | null;
  quality_reasons: { key: string; score: number; detail: string; region_ids: string[] }[];
  document_version: number;
  created_at: string;
  updated_at: string;
  expires_at: string | null;
  thumbnail_url: string | null;
  pages?: PageResponse[];
  exports?: ExportResponse[];
}

export interface ExportResponse {
  id: string;
  format: string;
  byte_size: number;
  created_at: string;
  expires_at: string | null;
  download_url?: string | null;
}

export interface AppConfig {
  brand_name: string;
  default_locale: string;
  locales: string[];
  tools: {
    slug: string;
    type: string;
    translates: boolean;
    accepts: string[];
    exports: string[];
    multipage: boolean;
    icon: string;
    i18n_key: string;
    credit_multiplier: number;
  }[];
  languages: {
    code: string;
    name_en: string;
    name_native: string;
    script: string;
    rtl: boolean;
    cjk: boolean;
    ocr: boolean;
    translation: boolean;
  }[];
  plans: {
    code: string;
    name: string;
    monthly_credits: number;
    price_usd_cents: number;
    price_rub_kopecks: number;
    max_upload_bytes: number;
    max_pdf_pages: number;
    export_formats: string[];
    features: string[];
  }[];
  credit_rules: Record<string, unknown>[];
  limits: Record<string, unknown>;
  features: Record<string, boolean>;
  translation_available: boolean;
  local_only_processing: boolean;
  maintenance_mode: boolean;
}

export interface SeoPageResponse {
  path: string;
  locale: string;
  kind: string;
  title: string;
  description: string;
  h1: string;
  intro: string | null;
  body_sections: { type: string; items?: string[]; key?: string }[];
  faq: { q: string; a: string }[];
  tool_slug: string | null;
  source_language: string | null;
  target_language: string | null;
  noindex: boolean;
}
