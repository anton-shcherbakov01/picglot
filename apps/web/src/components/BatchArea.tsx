"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  API_URL,
  ApiError,
  apiFetch,
  followJob,
  uploadBatch,
  type AppConfig,
  type JobResponse,
} from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { localePath, type Locale } from "@/lib/i18n";
import { format, type Messages } from "@/lib/messages";

type Phase = "idle" | "uploading" | "processing" | "done" | "error";

interface ChildJob {
  id: string;
  project_id: string | null;
  status: string;
  stage: string | null;
  pages_completed: number;
  pages_failed: number;
  error?: { code?: string; message?: string } | null;
  output?: Record<string, unknown>;
}

/**
 * Batch: drop a folder or many files, one child job per file.
 *
 * Per-file state comes from the parent job's children rather than from local
 * bookkeeping, so a reload shows the truth and a partially failed batch still
 * yields the files that did work.
 */
export function BatchArea({
  locale,
  messages,
  config,
}: {
  locale: Locale;
  messages: Messages;
  config: AppConfig;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [phase, setPhase] = useState<Phase>("idle");
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fraction, setFraction] = useState(0);
  const [parent, setParent] = useState<JobResponse | null>(null);
  const [children, setChildren] = useState<ChildJob[]>([]);
  const [target, setTarget] = useState("ru");
  const [translate, setTranslate] = useState(true);

  const abortRef = useRef<AbortController | null>(null);
  const stopRef = useRef<(() => void) | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => () => stopRef.current?.(), []);

  const maxFiles =
    (config.limits?.["max_batch_files"] as number | undefined) ?? 50;

  const addFiles = useCallback(
    (incoming: FileList | null) => {
      if (!incoming) return;
      setError(null);
      setFiles((current) => {
        const merged = [...current];
        for (const file of Array.from(incoming)) {
          if (
            !merged.some(
              (item) => item.name === file.name && item.size === file.size,
            )
          ) {
            merged.push(file);
          }
        }
        if (merged.length > maxFiles) {
          setError(messages.errors.batch_limit_exceeded);
          return merged.slice(0, maxFiles);
        }
        return merged;
      });
    },
    [maxFiles, messages.errors.batch_limit_exceeded],
  );

  const loadChildren = useCallback(async (parentId: string) => {
    try {
      const list = await apiFetch<{ items: ChildJob[] }>(
        `/api/v1/jobs?parent_job_id=${encodeURIComponent(parentId)}&limit=100`,
      );
      setChildren(list.items ?? []);
    } catch {
      /* the parent's own progress is still shown */
    }
  }, []);

  const start = async () => {
    if (files.length === 0) return;
    setPhase("uploading");
    setError(null);
    setFraction(0);
    abortRef.current = new AbortController();

    try {
      const created = await uploadBatch(
        files,
        {
          tool: "image-translator",
          target_language: translate ? target : null,
          translate,
          export_formats: [],
        },
        { onProgress: setFraction, signal: abortRef.current.signal },
      );
      setParent(created);
      setPhase("processing");
      void loadChildren(created.id);

      stopRef.current = followJob(created.id, {
        onProgress: (update) => {
          setParent((current) => ({ ...current, ...update }) as JobResponse);
          void loadChildren(created.id);
        },
        onDone: (finished) => {
          setParent(finished as JobResponse);
          setPhase("done");
          void loadChildren(created.id);
        },
        onError: (failure) => {
          setError(failure.message);
          setPhase("error");
        },
      });
    } catch (failure) {
      setError((failure as ApiError).message);
      setPhase("error");
    }
  };

  const cancel = async () => {
    abortRef.current?.abort();
    stopRef.current?.();
    if (parent) {
      try {
        await apiFetch(`/api/v1/jobs/${parent.id}/cancel`, { method: "POST" });
      } catch {
        /* already finished */
      }
    }
    setPhase("idle");
  };

  const retryFailed = async () => {
    if (!parent) return;
    try {
      const updated = await apiFetch<JobResponse>(
        `/api/v1/jobs/${parent.id}/retry-failed`,
        {
          method: "POST",
        },
      );
      setParent(updated);
      setPhase("processing");
      void loadChildren(parent.id);
    } catch (failure) {
      setError((failure as ApiError).message);
    }
  };

  // The parent's ZIP is an ordinary export; `report.csv` lives inside it.
  const output = parent?.output as Record<string, unknown> | undefined;
  const archiveId = output?.["archive_export_id"] as string | undefined;
  const zipUrl = archiveId
    ? `${API_URL}/api/v1/exports/${archiveId}/download`
    : undefined;
  const filesFailed = Number(output?.["files_failed"] ?? 0);
  const failedCount =
    children.filter((child) => child.status === "failed").length || filesFailed;

  if (phase === "idle" || phase === "error") {
    return (
      <div className="grid gap-4">
        {error && (
          <p
            role="alert"
            className="rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger"
          >
            {error}
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
            addFiles(event.dataTransfer.files);
          }}
          className={`rounded-card border-2 border-dashed p-10 text-center transition-colors ${
            dragging ? "border-accent bg-accent/5" : "border-border"
          }`}
        >
          <p className="font-medium">{messages.upload.dropTitle}</p>
          <p className="mt-1 text-sm text-muted">
            {messages.upload.dropSubtitle}
          </p>
          <div className="mt-4 flex flex-wrap justify-center gap-2">
            <button
              type="button"
              className="btn-secondary text-sm"
              onClick={() => inputRef.current?.click()}
            >
              {messages.upload.browse}
            </button>
          </div>
          <input
            ref={inputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(event) => addFiles(event.target.files)}
          />
          <p className="mt-3 text-xs text-muted">
            {format(messages.upload.maxSize, { size: `${maxFiles} files` })} ·{" "}
            {messages.upload.privacy}
          </p>
        </div>

        {files.length > 0 && (
          <div className="card p-5">
            <ul className="grid gap-1 text-sm">
              {files.map((file) => (
                <li
                  key={`${file.name}-${file.size}`}
                  className="flex justify-between gap-3"
                >
                  <span className="truncate">{file.name}</span>
                  <span className="shrink-0 text-xs text-muted">
                    {formatBytes(file.size, locale)}
                  </span>
                </li>
              ))}
            </ul>

            <div className="mt-4 flex flex-wrap items-end gap-3 border-t border-border pt-4">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={translate}
                  onChange={(event) => setTranslate(event.target.checked)}
                />
                {messages.common.targetLanguage}
              </label>
              {translate && (
                <select
                  className="input w-auto"
                  value={target}
                  onChange={(event) => setTarget(event.target.value)}
                  aria-label={messages.common.targetLanguage}
                >
                  {config.languages.map((language) => (
                    <option key={language.code} value={language.code}>
                      {language.name_native}
                    </option>
                  ))}
                </select>
              )}
              <button
                type="button"
                className="btn-primary text-sm"
                onClick={start}
              >
                {messages.home.ctaPrimary}
              </button>
              <button
                type="button"
                className="btn-ghost text-sm"
                onClick={() => {
                  setFiles([]);
                  setError(null);
                }}
              >
                {messages.common.cancel}
              </button>
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="grid gap-4">
      <div className="card p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="font-medium">
            {phase === "uploading"
              ? messages.upload.uploading
              : messages.processing.queued}
          </p>
          <span className="text-sm text-muted">
            {children.filter((child) => child.status === "completed").length} /{" "}
            {files.length}
          </span>
        </div>
        <div
          className="mt-3 h-2 overflow-hidden rounded-full bg-raised"
          role="progressbar"
          aria-valuenow={Math.round(
            (phase === "uploading" ? fraction : (parent?.progress ?? 0)) * 100,
          )}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className="h-full bg-accent transition-all"
            style={{
              width: `${Math.round((phase === "uploading" ? fraction : (parent?.progress ?? 0)) * 100)}%`,
            }}
          />
        </div>

        {phase !== "done" && (
          <button
            type="button"
            className="btn-ghost mt-3 text-xs"
            onClick={cancel}
          >
            {messages.processing.cancel}
          </button>
        )}
      </div>

      {children.length > 0 && (
        <div className="card p-5">
          <h2 className="text-sm font-semibold">{messages.dashboard.title}</h2>
          <ul className="mt-3 grid gap-2 text-sm">
            {children.map((child, index) => (
              <li
                key={child.id}
                className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-2 first:border-0 first:pt-0"
              >
                <span className="truncate">
                  {files[index]?.name ?? child.id}
                </span>
                <span className="flex items-center gap-2">
                  <span
                    className={`text-xs ${
                      child.status === "failed"
                        ? "text-danger"
                        : child.status === "completed"
                          ? "text-ok"
                          : "text-muted"
                    }`}
                  >
                    {(messages.processing[
                      (child.stage ??
                        child.status) as keyof Messages["processing"]
                    ] as string) ?? child.status}
                  </span>
                  {child.project_id && child.status === "completed" && (
                    <a
                      className="text-xs text-accent underline"
                      href={localePath(
                        locale,
                        `app/projects/${child.project_id}`,
                      )}
                    >
                      {messages.result.edit}
                    </a>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {phase === "done" && (
        <div className="card p-5">
          <h2 className="text-sm font-semibold">{messages.result.heading}</h2>
          <div className="mt-3 flex flex-wrap gap-2">
            {zipUrl && (
              <a className="btn-primary text-sm" href={zipUrl}>
                {format(messages.result.downloadAs, { format: "ZIP" })}
              </a>
            )}
            {failedCount > 0 && (
              <button
                type="button"
                className="btn-secondary text-sm"
                onClick={retryFailed}
              >
                {messages.processing.retry} ({failedCount})
              </button>
            )}
            <button
              type="button"
              className="btn-ghost text-sm"
              onClick={() => {
                setFiles([]);
                setChildren([]);
                setParent(null);
                setPhase("idle");
              }}
            >
              {messages.result.newFile}
            </button>
          </div>
          {failedCount > 0 && (
            <p className="mt-3 text-xs text-muted">
              {messages.processing.partially_completed}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
