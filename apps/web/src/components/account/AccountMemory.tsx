"use client";

import { useEffect, useState } from "react";

import { ApiError, apiFetch } from "@/lib/api";

import { ErrorNote, type SectionProps } from "./AccountPanel";

interface MemoryEntry {
  id: string;
  source_language: string;
  target_language: string;
  source_text: string;
  target_text: string;
  [key: string]: unknown;
}

export function AccountMemory({ messages, onError }: SectionProps) {
  const [rows, setRows] = useState<MemoryEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const load = () => {
    apiFetch<MemoryEntry[]>("/api/v1/account/translation-memory?limit=100")
      .then((data) => {
        setRows(data);
        setError(null);
      })
      .catch((failure) => {
        if (!onError(failure)) setError((failure as ApiError).message);
      });
  };

  useEffect(load, [onError]);

  const remove = async (id: string) => {
    if (!window.confirm(`${messages.common.delete}?`)) return;
    try {
      await apiFetch(`/api/v1/account/translation-memory/${id}`, {
        method: "DELETE",
      });
      setRows((current) => current.filter((row) => row.id !== id));
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    }
  };

  const needle = query.trim().toLowerCase();
  const visible = needle
    ? rows.filter(
        (row) =>
          row.source_text.toLowerCase().includes(needle) ||
          row.target_text.toLowerCase().includes(needle),
      )
    : rows;

  return (
    <div className="grid gap-4">
      {error && <ErrorNote message={error} />}

      <div>
        <label className="label" htmlFor="tm-search">
          {messages.dashboard.search}
        </label>
        <input
          id="tm-search"
          type="search"
          className="input"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>

      <div className="card p-5">
        {visible.length === 0 ? (
          <p className="text-sm text-muted">{messages.dashboard.empty}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-muted">
                  <th className="py-2 pr-3 font-medium">Pair</th>
                  <th className="py-2 pr-3 font-medium">
                    {messages.editor.sourceText}
                  </th>
                  <th className="py-2 pr-3 font-medium">
                    {messages.editor.translatedText}
                  </th>
                  <th className="py-2 font-medium" />
                </tr>
              </thead>
              <tbody>
                {visible.map((row) => (
                  <tr key={row.id} className="border-t border-border align-top">
                    <td className="whitespace-nowrap py-2 pr-3 text-xs text-muted">
                      {row.source_language} → {row.target_language}
                    </td>
                    <td className="py-2 pr-3">{row.source_text}</td>
                    <td className="py-2 pr-3">{row.target_text}</td>
                    <td className="py-2 text-right">
                      <button
                        type="button"
                        className="btn-ghost text-xs text-danger"
                        onClick={() => void remove(row.id)}
                      >
                        {messages.common.delete}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
