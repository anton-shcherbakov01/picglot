'use client';

import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';

import { ApiError, apiFetch, followJob, type AppConfig, type ProjectResponse } from '@/lib/api';
import { localePath, type Locale } from '@/lib/i18n';
import type { Messages } from '@/lib/messages';
import { Editor } from './Editor';

export function ProjectView({
  projectId,
  locale,
  messages,
  config,
}: {
  projectId: string;
  locale: Locale;
  messages: Messages;
  config: AppConfig;
}) {
  const router = useRouter();
  const [project, setProject] = useState<ProjectResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const result = await apiFetch<ProjectResponse>(`/api/v1/projects/${projectId}`);
      setProject(result);
      setError(null);
      return result;
    } catch (failure) {
      const apiError = failure as ApiError;
      if (apiError.status === 401) {
        router.push(localePath(locale, 'auth/sign-in'));
        return null;
      }
      setError(
        (messages.errors as Record<string, string>)[apiError.code] ?? apiError.message,
      );
      return null;
    }
  }, [projectId, locale, router, messages.errors]);

  useEffect(() => {
    void (async () => {
      const result = await load();
      // Opening a project mid-run should show live progress, not a stale page.
      const active = (result as (ProjectResponse & { active_job?: { id: string; status: string } }) | null)
        ?.active_job;
      if (active && !['completed', 'partially_completed', 'failed', 'cancelled'].includes(active.status)) {
        followJob(active.id, {
          onProgress: (update) => setProgress(update.progress),
          onDone: () => {
            setProgress(null);
            void load();
          },
          onError: () => setProgress(null),
        });
      }
    })();
  }, [load]);

  if (error) {
    return (
      <p role="alert" className="rounded-card border border-danger/40 bg-danger/10 px-4 py-3 text-sm text-danger">
        {error}
      </p>
    );
  }

  if (!project) {
    return <div className="skeleton h-96 rounded-card" />;
  }

  return (
    <>
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">{project.name}</h1>
        <span className="chip">{project.tool_type}</span>
        {progress !== null && (
          <span className="chip" role="status">
            {Math.round(progress * 100)}%
          </span>
        )}
      </div>
      <Editor
        messages={messages}
        config={config}
        project={project}
        onReload={async () => {
          await load();
        }}
        onReset={() => router.push(localePath(locale, 'app'))}
      />
    </>
  );
}
