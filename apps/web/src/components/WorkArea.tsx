"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  apiFetch,
  followJob,
  uploadAndProcess,
  type AppConfig,
  type JobResponse,
  type ProjectResponse,
} from "@/lib/api";
import { formatBytes, sizeBucket } from "@/lib/format";
import type { Locale } from "@/lib/i18n";
import { format, type Messages } from "@/lib/messages";
import { track } from "@/lib/analytics";
import { Editor } from "./Editor";

type Phase = "idle" | "uploading" | "processing" | "done" | "error";

interface Props {
  locale: Locale;
  messages: Messages;
  config: AppConfig;
  toolSlug: string;
  defaultSource?: string | null;
  defaultTarget?: string | null;
}

export function WorkArea({
  locale,
  messages,
  config,
  toolSlug,
  defaultSource,
  defaultTarget,
}: Props) {
  const tool =
    config.tools.find((item) => item.slug === toolSlug) ?? config.tools[0];
  const [phase, setPhase] = useState<Phase>("idle");
  const [file, setFile] = useState<File | null>(null);
  const [uploadFraction, setUploadFraction] = useState(0);
  const [job, setJob] = useState<Partial<JobResponse> | null>(null);
  const [project, setProject] = useState<ProjectResponse | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [source, setSource] = useState(defaultSource ?? "auto");
  const [target, setTarget] = useState(
    defaultTarget ?? (locale === "ru" ? "ru" : "en"),
  );
  const [dragging, setDragging] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const stopFollowRef = useRef<(() => void) | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const cameraRef = useRef<HTMLInputElement>(null);

  const translates = tool?.translates ?? false;
  // Memoised: a fresh `[]` fallback each render would invalidate `validate`.
  const accepts = useMemo(() => tool?.accepts ?? [], [tool]);
  const acceptAttr = accepts.map((ext) => `.${ext}`).join(",");
  const maxBytes = Number(
    (config.limits.max_upload_bytes as Record<string, number> | undefined)
      ?.guest ?? 10485760,
  );

  useEffect(() => () => stopFollowRef.current?.(), []);

  // Ctrl/Cmd+V anywhere on the page starts a job — the screenshot workflow.
  useEffect(() => {
    const onPaste = (event: ClipboardEvent) => {
      if (phase === "uploading" || phase === "processing") return;
      const item = Array.from(event.clipboardData?.items ?? []).find((entry) =>
        entry.type.startsWith("image/"),
      );
      const pasted = item?.getAsFile();
      if (pasted) {
        event.preventDefault();
        void start(
          new File([pasted], `pasted-${Date.now()}.png`, { type: pasted.type }),
        );
      }
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  });

  const reset = () => {
    stopFollowRef.current?.();
    setPhase("idle");
    setFile(null);
    setJob(null);
    setProject(null);
    setError(null);
    setUploadFraction(0);
  };

  /** Client-side pre-check so an obviously wrong file never costs an upload. */
  const validate = useCallback(
    (candidate: File): ApiError | null => {
      const extension = candidate.name.split(".").pop()?.toLowerCase() ?? "";
      if (accepts.length && !accepts.includes(extension)) {
        return new ApiError({
          code: "unsupported_file_type",
          message: messages.errors.unsupported_file_type,
          status: 415,
        });
      }
      if (candidate.size > maxBytes) {
        return new ApiError({
          code: "file_too_large",
          message: messages.errors.file_too_large,
          status: 413,
        });
      }
      return null;
    },
    [accepts, maxBytes, messages.errors],
  );

  const start = useCallback(
    async (candidate: File) => {
      const invalid = validate(candidate);
      if (invalid) {
        setError(invalid);
        setPhase("error");
        return;
      }

      setFile(candidate);
      setError(null);
      setPhase("uploading");
      setUploadFraction(0);
      abortRef.current = new AbortController();

      track("upload_started", {
        tool: toolSlug,
        file_type: candidate.type,
        file_size_bucket: sizeBucket(candidate.size),
      });

      try {
        const created = await uploadAndProcess(
          candidate,
          {
            tool: tool?.type ?? toolSlug,
            source_language: source === "auto" ? null : source,
            target_language: translates ? target : null,
            translate: translates,
            export_formats: [],
          },
          {
            onProgress: setUploadFraction,
            signal: abortRef.current.signal,
          },
        );
        track("upload_completed", { tool: toolSlug });
        setJob(created);
        setPhase("processing");

        stopFollowRef.current = followJob(created.id, {
          onProgress: (update) =>
            setJob((current) => ({ ...current, ...update })),
          onDone: async (finished) => {
            setJob(finished);
            track("processing_completed", {
              tool: toolSlug,
              pages: finished.pages_completed,
            });
            if (finished.project_id) {
              try {
                setProject(
                  await apiFetch<ProjectResponse>(
                    `/api/v1/projects/${finished.project_id}`,
                  ),
                );
              } catch (loadError) {
                setError(loadError as ApiError);
              }
            }
            setPhase("done");
          },
          onError: (failure) => {
            track("processing_failed", {
              tool: toolSlug,
              error_code: failure.code,
            });
            setError(failure);
            setPhase("error");
          },
        });
      } catch (uploadError) {
        const failure = uploadError as ApiError;
        track("upload_failed", { tool: toolSlug, error_code: failure.code });
        setError(failure);
        setPhase(failure.code === "cancelled" ? "idle" : "error");
      }
    },
    [source, target, toolSlug, translates, tool, validate],
  );

  const cancel = async () => {
    abortRef.current?.abort();
    stopFollowRef.current?.();
    if (job?.id) {
      try {
        await apiFetch(`/api/v1/jobs/${job.id}/cancel`, { method: "POST" });
      } catch {
        /* the job may already have finished */
      }
    }
    reset();
  };

  // ---------------------------------------------------------------- render
  if (phase === "done" && project) {
    return (
      <Editor
        messages={messages}
        config={config}
        project={project}
        onReload={async () => {
          setProject(
            await apiFetch<ProjectResponse>(`/api/v1/projects/${project.id}`),
          );
        }}
        onReset={reset}
      />
    );
  }

  if (phase === "uploading" || phase === "processing") {
    return (
      <ProgressPanel
        messages={messages}
        phase={phase}
        uploadFraction={uploadFraction}
        job={job}
        fileName={file?.name ?? ""}
        onCancel={cancel}
      />
    );
  }

  return (
    <div className="grid gap-4">
      {error && (
        <ErrorBanner messages={messages} error={error} onRetry={reset} />
      )}

      {translates && (
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor="source-language" className="label">
              {messages.common.sourceLanguage}
            </label>
            <select
              id="source-language"
              className="input"
              value={source}
              onChange={(event) => setSource(event.target.value)}
            >
              <option value="auto">{messages.common.autoDetect}</option>
              {config.languages
                .filter((language) => language.ocr)
                .map((language) => (
                  <option key={language.code} value={language.code}>
                    {language.name_native}
                  </option>
                ))}
            </select>
          </div>
          <div>
            <label htmlFor="target-language" className="label">
              {messages.common.targetLanguage}
            </label>
            <select
              id="target-language"
              className="input"
              value={target}
              onChange={(event) => setTarget(event.target.value)}
            >
              {config.languages
                .filter((language) => language.translation)
                .map((language) => (
                  <option key={language.code} value={language.code}>
                    {language.name_native}
                  </option>
                ))}
            </select>
          </div>
        </div>
      )}

      {translates && !config.translation_available && (
        <p
          role="status"
          className="rounded-lg border border-warn/40 bg-warn/10 px-4 py-3 text-sm text-warn"
        >
          Translation is not configured on this installation yet. Recognition,
          editing and exports work; translating needs a provider key or the
          offline models.
        </p>
      )}

      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          const dropped = event.dataTransfer.files?.[0];
          if (dropped) void start(dropped);
        }}
        className={`rounded-panel border border-dashed p-6 text-center shadow-soft transition-[border-color,background-color] sm:p-10 ${
          dragging
            ? "border-accent bg-accent/5"
            : "border-border bg-surface hover:border-accent/40"
        }`}
      >
        <span
          aria-hidden
          className={`mx-auto grid h-14 w-14 place-items-center rounded-2xl border transition-colors ${
            dragging
              ? "border-accent/40 bg-accent/10 text-accent"
              : "border-border bg-raised text-muted"
          }`}
        >
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none">
            <path
              d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <path
              d="M3.5 15v2.5A2.5 2.5 0 0 0 6 20h12a2.5 2.5 0 0 0 2.5-2.5V15"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinecap="round"
            />
          </svg>
        </span>
        <p className="mt-4 text-lg font-semibold tracking-tight">
          {dragging ? messages.upload.dragActive : messages.upload.dropTitle}
        </p>
        <p className="mt-1 text-sm text-muted">
          {messages.upload.dropSubtitle}
        </p>

        <div className="mt-6 flex flex-col items-stretch gap-2 sm:flex-row sm:justify-center">
          <button
            type="button"
            className="btn-primary"
            onClick={() => inputRef.current?.click()}
          >
            {messages.upload.browse}
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => cameraRef.current?.click()}
          >
            {messages.upload.camera}
          </button>
        </div>
        {/* Paste is a keyboard shortcut; on a touch device it is noise. */}
        <p className="meta mt-4 hidden sm:block">
          ⌘/Ctrl + V — {messages.upload.paste}
        </p>

        <input
          ref={inputRef}
          type="file"
          accept={acceptAttr}
          className="sr-only"
          onChange={(event) => {
            const chosen = event.target.files?.[0];
            if (chosen) void start(chosen);
            event.target.value = "";
          }}
        />
        <input
          ref={cameraRef}
          type="file"
          accept="image/*"
          capture="environment"
          className="sr-only"
          onChange={(event) => {
            const chosen = event.target.files?.[0];
            if (chosen) void start(chosen);
            event.target.value = "";
          }}
        />

        <div className="rule-fade mx-auto mt-6 max-w-sm" />
        <p className="meta mt-4">
          {format(messages.upload.maxSize, {
            size: formatBytes(maxBytes, locale),
          })}{" "}
          · {accepts.join(" · ")}
        </p>
        <p className="mt-2 text-xs text-muted">{messages.upload.privacy}</p>
      </div>
    </div>
  );
}

function ProgressPanel({
  messages,
  phase,
  uploadFraction,
  job,
  fileName,
  onCancel,
}: {
  messages: Messages;
  phase: Phase;
  uploadFraction: number;
  job: Partial<JobResponse> | null;
  fileName: string;
  onCancel: () => void;
}) {
  const stage = (job?.stage ??
    job?.status ??
    "queued") as keyof Messages["processing"];
  const label =
    phase === "uploading"
      ? messages.upload.uploading
      : ((messages.processing[stage] as string) ?? messages.processing.queued);
  const percent =
    phase === "uploading"
      ? Math.round(uploadFraction * 100)
      : Math.round((job?.progress ?? 0) * 100);

  return (
    <div className="card p-8 text-center">
      <p className="meta truncate">{fileName}</p>
      <p
        aria-live="polite"
        className="mt-3 text-lg font-semibold tracking-tight"
      >
        {label}
      </p>

      <div
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
        className="mx-auto mt-5 h-1.5 w-full max-w-md overflow-hidden rounded-full bg-raised"
      >
        <div
          className="h-full rounded-full bg-gradient-to-r from-accent to-mint transition-[width] duration-500"
          style={{ width: `${Math.max(3, percent)}%` }}
        />
      </div>
      <output className="mt-2 block text-sm text-muted">{percent}%</output>

      {job?.pages_total && job.pages_total > 1 && (
        <p className="mt-1 text-xs text-muted">
          {format(messages.processing.page, {
            current: job.pages_completed ?? 0,
            total: job.pages_total,
          })}
        </p>
      )}

      <button type="button" className="btn-ghost mt-6" onClick={onCancel}>
        {messages.processing.cancel}
      </button>
    </div>
  );
}

function ErrorBanner({
  messages,
  error,
  onRetry,
}: {
  messages: Messages;
  error: ApiError;
  onRetry: () => void;
}) {
  const localized =
    (messages.errors as Record<string, string>)[error.code] ?? error.message;

  return (
    <div
      role="alert"
      className="rounded-card border border-danger/40 bg-danger/10 p-4 text-sm text-danger"
    >
      <p className="font-semibold">{localized}</p>
      {typeof error.details.limit_mb === "number" && (
        <p className="mt-1 opacity-90">
          Limit: {String(error.details.limit_mb)} MB
        </p>
      )}
      {typeof error.details.remaining_pages === "number" && (
        <p className="mt-1 opacity-90">
          {format(messages.upload.remaining, {
            count: String(error.details.remaining_pages),
          })}
        </p>
      )}
      <div className="mt-3 flex items-center gap-3">
        {error.retryable && (
          <button type="button" className="btn-secondary" onClick={onRetry}>
            {messages.errors.retry}
          </button>
        )}
        {error.requestId && (
          <span className="text-xs opacity-75">
            {format(messages.errors.requestId, { id: error.requestId })}
          </span>
        )}
      </div>
    </div>
  );
}
