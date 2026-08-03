"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ApiError, apiFetch, type ProjectResponse } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { localePath, type Locale } from "@/lib/i18n";
import type { Messages } from "@/lib/messages";

interface ProjectList {
  items: ProjectResponse[];
  total: number;
  limit: number;
  offset: number;
}

interface Wallet {
  balance: number;
  lifetime_spent: number;
  plan_code: string;
}

export function Dashboard({
  locale,
  messages,
}: {
  locale: Locale;
  messages: Messages;
}) {
  const router = useRouter();
  const [projects, setProjects] = useState<ProjectList | null>(null);
  const [wallet, setWallet] = useState<Wallet | null>(null);
  const [search, setSearch] = useState("");
  const [trash, setTrash] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const query = new URLSearchParams({ limit: "24" });
      if (search) query.set("search", search);
      if (trash) query.set("trash", "true");
      const [list, balance] = await Promise.all([
        apiFetch<ProjectList>(`/api/v1/projects?${query}`),
        apiFetch<Wallet>("/api/v1/account/wallet").catch(() => null),
      ]);
      setProjects(list);
      setWallet(balance);
      setError(null);
    } catch (failure) {
      const apiError = failure as ApiError;
      if (apiError.status === 401) {
        router.push(localePath(locale, "auth/sign-in"));
        return;
      }
      setError(
        (messages.errors as Record<string, string>)[apiError.code] ??
          apiError.message,
      );
    } finally {
      setLoading(false);
    }
  }, [search, trash, locale, router, messages.errors]);

  useEffect(() => {
    const timer = setTimeout(() => void load(), search ? 300 : 0);
    return () => clearTimeout(timer);
  }, [load, search]);

  const remove = async (id: string) => {
    await apiFetch(`/api/v1/projects/${id}`, { method: "DELETE" });
    await load();
  };

  const restore = async (id: string) => {
    await apiFetch(`/api/v1/projects/${id}/restore`, { method: "POST" });
    await load();
  };

  return (
    <div className="container-page py-10">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold">{messages.dashboard.title}</h1>
        {wallet && (
          <span className="chip">
            {messages.dashboard.credits.replace(
              "{count}",
              String(wallet.balance),
            )}
          </span>
        )}
        <Link
          href={localePath(locale, "image-translator")}
          className="btn-primary ml-auto"
        >
          {messages.dashboard.newProject}
        </Link>
      </div>

      <div className="mt-6 flex flex-wrap items-center gap-3">
        <label htmlFor="project-search" className="sr-only">
          {messages.dashboard.search}
        </label>
        <input
          id="project-search"
          type="search"
          className="input max-w-sm"
          placeholder={messages.dashboard.search}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <button
          type="button"
          className={trash ? "btn-secondary" : "btn-ghost"}
          aria-pressed={trash}
          onClick={() => setTrash((value) => !value)}
        >
          {messages.dashboard.trash}
        </button>
      </div>

      {error && (
        <p
          role="alert"
          className="mt-6 rounded-lg border border-danger/40 bg-danger/10 px-4 py-3 text-sm text-danger"
        >
          {error}
        </p>
      )}

      {loading && !projects ? (
        <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map((index) => (
            <div key={index} className="skeleton h-52 rounded-card" />
          ))}
        </div>
      ) : projects && projects.items.length > 0 ? (
        <ul className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.items.map((project) => (
            <li key={project.id} className="card flex flex-col overflow-hidden">
              <Link
                href={localePath(locale, `app/projects/${project.id}`)}
                className="block bg-raised"
              >
                {project.thumbnail_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={project.thumbnail_url}
                    alt=""
                    className="h-36 w-full object-cover"
                    loading="lazy"
                  />
                ) : (
                  <div
                    className="grid h-36 place-items-center text-3xl"
                    aria-hidden
                  >
                    🗒️
                  </div>
                )}
              </Link>
              <div className="flex flex-1 flex-col p-4">
                <Link
                  href={localePath(locale, `app/projects/${project.id}`)}
                  className="font-medium hover:text-accent"
                >
                  <span className="line-clamp-1">{project.name}</span>
                </Link>
                <p className="mt-1 text-xs text-muted">
                  {project.tool_type} · {project.page_count} p.
                  {project.quality_band ? ` · ${project.quality_band}` : ""}
                </p>
                <p className="mt-1 text-xs text-muted">
                  {formatRelative(project.updated_at, locale)}
                </p>
                {project.expires_at && !trash && (
                  <p className="mt-1 text-xs text-warn">
                    {messages.dashboard.expires.replace(
                      "{when}",
                      formatRelative(project.expires_at, locale),
                    )}
                  </p>
                )}
                <div className="mt-3 flex gap-2 text-xs">
                  {trash ? (
                    <button
                      type="button"
                      className="btn-ghost px-2 py-1"
                      onClick={() => void restore(project.id)}
                    >
                      {messages.dashboard.restore}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="btn-ghost px-2 py-1 text-danger"
                      onClick={() => void remove(project.id)}
                    >
                      {messages.common.delete}
                    </button>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <div className="card mt-6 p-10 text-center">
          <p className="text-muted">{messages.dashboard.empty}</p>
          <Link
            href={localePath(locale, "image-translator")}
            className="btn-primary mt-4 inline-flex"
          >
            {messages.dashboard.newProject}
          </Link>
        </div>
      )}
    </div>
  );
}
