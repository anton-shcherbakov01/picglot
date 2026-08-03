"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, apiFetch, type AppConfig } from "@/lib/api";
import { formatDate } from "@/lib/format";

import { ErrorNote, Notice, type SectionProps } from "./AccountPanel";

interface Glossary {
  id: string;
  name: string;
  source_language: string;
  target_language: string;
  version: number;
  is_default: boolean;
  term_count: number;
  created_at: string;
}

interface Term {
  source_term: string;
  target_term: string;
  case_sensitive?: boolean;
  whole_word?: boolean;
  do_not_translate?: boolean;
  note?: string | null;
}

/** `source<TAB or comma>target` per line — the same shape the CSV import uses. */
function parseTerms(raw: string): Term[] {
  return raw
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [source = "", target = ""] = line
        .split(/\t|,/, 2)
        .map((part) => part.trim());
      return { source_term: source, target_term: target };
    })
    .filter((term) => term.source_term.length > 0);
}

export function AccountGlossaries({ locale, messages, onError }: SectionProps) {
  const [rows, setRows] = useState<Glossary[]>([]);
  const [languages, setLanguages] = useState<AppConfig["languages"]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [source, setSource] = useState("en");
  const [target, setTarget] = useState("ru");
  const [editing, setEditing] = useState<string | null>(null);
  const [terms, setTerms] = useState("");

  const load = useCallback(() => {
    apiFetch<Glossary[]>("/api/v1/account/glossaries")
      .then((data) => {
        setRows(data);
        setError(null);
      })
      .catch((failure) => {
        if (!onError(failure)) setError((failure as ApiError).message);
      });
  }, [onError]);

  useEffect(() => {
    load();
    apiFetch<AppConfig>("/api/v1/config")
      .then((config) => setLanguages(config.languages))
      .catch(() => setLanguages([]));
  }, [load]);

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await apiFetch<Glossary>("/api/v1/account/glossaries", {
        method: "POST",
        json: {
          name: name.trim(),
          source_language: source,
          target_language: target,
        },
      });
      setName("");
      load();
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  const saveTerms = async (id: string) => {
    const parsed = parseTerms(terms);
    setBusy(true);
    setError(null);
    try {
      await apiFetch<Glossary>(`/api/v1/account/glossaries/${id}/terms`, {
        method: "PUT",
        json: { terms: parsed },
      });
      setNotice(`${messages.editor.saved} (${parsed.length})`);
      setEditing(null);
      setTerms("");
      load();
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-4">
      {error && <ErrorNote message={error} />}
      {notice && <Notice message={notice} />}

      <form
        onSubmit={create}
        className="card flex flex-wrap items-end gap-3 p-5"
      >
        <div className="min-w-48 flex-1">
          <label className="label" htmlFor="glossary-name">
            {messages.editor.title}
          </label>
          <input
            id="glossary-name"
            className="input"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
            minLength={2}
          />
        </div>
        <div>
          <label className="label" htmlFor="glossary-source">
            {messages.common.from}
          </label>
          <select
            id="glossary-source"
            className="input w-auto"
            value={source}
            onChange={(event) => setSource(event.target.value)}
          >
            {languages.map((language) => (
              <option key={language.code} value={language.code}>
                {language.name_en}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="glossary-target">
            {messages.common.to}
          </label>
          <select
            id="glossary-target"
            className="input w-auto"
            value={target}
            onChange={(event) => setTarget(event.target.value)}
          >
            {languages.map((language) => (
              <option key={language.code} value={language.code}>
                {language.name_en}
              </option>
            ))}
          </select>
        </div>
        <button type="submit" className="btn-primary text-sm" disabled={busy}>
          {messages.dashboard.newProject}
        </button>
      </form>

      {rows.length === 0 ? (
        <p className="text-sm text-muted">{messages.dashboard.empty}</p>
      ) : (
        rows.map((glossary) => (
          <div key={glossary.id} className="card p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="font-medium">
                  {glossary.name}
                  {glossary.is_default && (
                    <span className="chip ml-2">default</span>
                  )}
                </p>
                <p className="mt-1 text-xs text-muted">
                  {glossary.source_language} → {glossary.target_language} ·{" "}
                  {glossary.term_count} terms · v{glossary.version} ·{" "}
                  {formatDate(glossary.created_at, locale)}
                </p>
              </div>
              <button
                type="button"
                className="btn-ghost text-xs"
                onClick={() => {
                  setEditing(editing === glossary.id ? null : glossary.id);
                  setTerms("");
                }}
              >
                {editing === glossary.id
                  ? messages.common.close
                  : messages.result.edit}
              </button>
            </div>

            {editing === glossary.id && (
              <div className="mt-4 border-t border-border pt-3">
                <label className="label" htmlFor={`terms-${glossary.id}`}>
                  {messages.dashboard.glossary} — one per line, `source,target`
                </label>
                <textarea
                  id={`terms-${glossary.id}`}
                  className="input min-h-40 font-mono text-xs"
                  value={terms}
                  onChange={(event) => setTerms(event.target.value)}
                  placeholder={"invoice,счёт\ndelivery note,накладная"}
                />
                <p className="mt-2 text-xs text-muted">
                  Replaces every term in this glossary and bumps its version.
                </p>
                <button
                  type="button"
                  className="btn-primary mt-3 text-xs"
                  disabled={busy}
                  onClick={() => void saveTerms(glossary.id)}
                >
                  {messages.common.save}
                </button>
              </div>
            )}
          </div>
        ))
      )}
    </div>
  );
}
