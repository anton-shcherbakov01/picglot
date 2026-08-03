"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  apiFetch,
  type AppConfig,
  type ExportResponse,
  type PageResponse,
  type ProjectResponse,
  type RegionResponse,
} from "@/lib/api";
import { track } from "@/lib/analytics";
import type { Messages } from "@/lib/messages";

import { BeforeAfterSlider } from "./editor/BeforeAfterSlider";
import type { BoxChange, CanvasTool, MaskStroke } from "./editor/CanvasStage";

/** Konva touches `window` on import, so the stage only loads in the browser. */
const CanvasStage = dynamic(
  () => import("./editor/CanvasStage").then((module) => module.CanvasStage),
  {
    ssr: false,
    loading: () => <div className="skeleton aspect-[4/3] w-full" />,
  },
);

type View = "result" | "original" | "compare";

/** One reversible block edit. Values are whatever the API accepted for those keys. */
interface HistoryEntry {
  regionId: string;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
}

const HISTORY_LIMIT = 100;

interface Props {
  messages: Messages;
  config: AppConfig;
  project: ProjectResponse;
  onReload: () => Promise<void>;
  onReset: () => void;
}

/**
 * Result view and block editor.
 *
 * Pointer work happens on a Konva canvas: pan, zoom, move/resize/rotate a
 * block, drag polygon vertices, paint an inpainting mask. The block list beside
 * it is the keyboard and screen-reader path — a canvas cannot carry an
 * accessibility tree, so the list is the accessible equivalent rather than an
 * afterthought, and text editing uses ordinary inputs there.
 *
 * Geometry edits go through `patchRegion` like any other change, so optimistic
 * version locking, the 100-step history and conflict reporting all apply to
 * dragging a box exactly as they do to retyping its text.
 */
export function Editor({
  messages,
  config,
  project,
  onReload,
  onReset,
}: Props) {
  // Memoised: a fresh `[]` each render would re-run every dependent hook.
  const pages = useMemo(() => project.pages ?? [], [project.pages]);
  const [pageIndex, setPageIndex] = useState(0);
  const [view, setView] = useState<View>(
    project.target_language ? "result" : "original",
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [historyIndex, setHistoryIndex] = useState(0);
  const [tool, setTool] = useState<CanvasTool>("select");
  const [brushSize, setBrushSize] = useState(24);
  const [showBoxes, setShowBoxes] = useState(true);
  const [showMask, setShowMask] = useState(true);
  const [maskStrokes, setMaskStrokes] = useState<MaskStroke[]>([]);

  const page: PageResponse | undefined = pages[pageIndex];
  const selected =
    page?.regions.find((region) => region.id === selectedId) ?? null;
  const imageWrapRef = useRef<HTMLDivElement>(null);

  const plainText = useMemo(
    () =>
      pages
        .flatMap((item) =>
          [...item.regions]
            .sort((a, b) => a.reading_order - b.reading_order)
            .map((region) =>
              view === "original"
                ? (region.normalized_text ?? region.source_text)
                : (region.translated_text ??
                  region.normalized_text ??
                  region.source_text),
            ),
        )
        .filter(Boolean)
        .join("\n"),
    [pages, view],
  );

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  useEffect(() => {
    track("editor_opened", { tool: project.tool_type });
  }, [project.tool_type]);

  const imageUrl =
    view === "original"
      ? (page?.original_url ?? page?.preview_url)
      : (page?.rendered_url ?? page?.preview_url ?? page?.original_url);

  // ------------------------------------------------------------ mutations
  const sendPatch = useCallback(
    async (region: RegionResponse, changes: Record<string, unknown>) => {
      setSaving(true);
      setError(null);
      try {
        await apiFetch(`/api/v1/projects/${project.id}/regions/${region.id}`, {
          method: "PATCH",
          json: { ...changes, version: region.version },
        });
        setDirty(true);
        await onReload();
        return true;
      } catch (failure) {
        const apiError = failure as ApiError;
        setError(
          apiError.code === "version_conflict"
            ? messages.editor.conflict
            : ((messages.errors as Record<string, string>)[apiError.code] ??
                apiError.message),
        );
        return false;
      } finally {
        setSaving(false);
      }
    },
    [project.id, onReload, messages],
  );

  const patchRegion = async (
    region: RegionResponse,
    changes: Record<string, unknown>,
  ) => {
    const before = Object.fromEntries(
      Object.keys(changes).map((key) => [
        key,
        (region as unknown as Record<string, unknown>)[key],
      ]),
    );
    if (!(await sendPatch(region, changes))) return;

    // A new edit discards any redo branch, exactly like a text editor.
    setHistory((entries) =>
      [
        ...entries.slice(0, historyIndex),
        { regionId: region.id, before, after: changes },
      ].slice(-HISTORY_LIMIT),
    );
    setHistoryIndex((index) => Math.min(index + 1, HISTORY_LIMIT));
    track("region_edited", { tool: project.tool_type });
  };

  /** Undo/redo replay inverse patches against the server — there is no local document to rewind. */
  const findRegion = useCallback(
    (regionId: string) =>
      pages
        .flatMap((item) => item.regions)
        .find((region) => region.id === regionId) ?? null,
    [pages],
  );

  const step = async (direction: -1 | 1) => {
    const entry =
      direction === -1 ? history[historyIndex - 1] : history[historyIndex];
    if (!entry) return;
    const region = findRegion(entry.regionId);
    if (!region) {
      setError(messages.editor.conflict);
      return;
    }
    if (
      !(await sendPatch(region, direction === -1 ? entry.before : entry.after))
    )
      return;
    setHistoryIndex((index) => index + direction);
  };

  // Geometry from the canvas goes through the same patch path as text, so it
  // inherits versioning, history and conflict handling.
  const handleBoxChange = (region: RegionResponse, box: BoxChange) => {
    void patchRegion(region, {
      bounding_box: {
        x: Math.round(box.x),
        y: Math.round(box.y),
        width: Math.round(box.width),
        height: Math.round(box.height),
      },
      rotation: Number(box.rotation.toFixed(2)),
    });
  };

  const handlePolygonChange = (region: RegionResponse, polygon: number[][]) => {
    void patchRegion(region, {
      polygon: polygon.map(([x, y]) => [
        Math.round(x ?? 0),
        Math.round(y ?? 0),
      ]),
    });
  };

  /** Flatten the brush strokes to a PNG the re-render endpoint can inpaint with. */
  const maskDataUrl = useCallback((): string | null => {
    if (!page || maskStrokes.length === 0) return null;
    const canvas = document.createElement("canvas");
    canvas.width = page.width;
    canvas.height = page.height;
    const context = canvas.getContext("2d");
    if (!context) return null;
    context.fillStyle = "#000000";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.lineCap = "round";
    context.lineJoin = "round";
    for (const stroke of maskStrokes) {
      context.globalCompositeOperation = stroke.erase
        ? "destination-out"
        : "source-over";
      context.strokeStyle = "#ffffff";
      context.lineWidth = stroke.size;
      context.beginPath();
      for (let index = 0; index + 1 < stroke.points.length; index += 2) {
        const x = stroke.points[index]!;
        const y = stroke.points[index + 1]!;
        if (index === 0) context.moveTo(x, y);
        else context.lineTo(x, y);
      }
      context.stroke();
    }
    return canvas.toDataURL("image/png");
  }, [page, maskStrokes]);

  const applyEdits = async () => {
    const mask = maskDataUrl();
    await runAction(`/api/v1/projects/${project.id}/rerender`, {
      render_mode: "translation_only",
      ...(mask && page ? { masks: { [page.id]: mask } } : {}),
    });
    setMaskStrokes([]);
  };

  const runAction = async (path: string, body?: Record<string, unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(path, { method: "POST", json: body ?? {} });
      await onReload();
      setDirty(false);
    } catch (failure) {
      const apiError = failure as ApiError;
      setError(
        (messages.errors as Record<string, string>)[apiError.code] ??
          apiError.message,
      );
    } finally {
      setBusy(false);
    }
  };

  const download = async (formatName: string) => {
    setBusy(true);
    setError(null);
    try {
      track("export_started", { tool: project.tool_type, format: formatName });
      const result = await apiFetch<ExportResponse>(
        `/api/v1/projects/${project.id}/exports`,
        { method: "POST", json: { format: formatName } },
      );
      if (result.download_url) {
        window.location.href = result.download_url;
        track("export_completed", {
          tool: project.tool_type,
          format: formatName,
        });
      }
    } catch (failure) {
      const apiError = failure as ApiError;
      setError(
        (messages.errors as Record<string, string>)[apiError.code] ??
          apiError.message,
      );
    } finally {
      setBusy(false);
    }
  };

  const toolExports = config.tools.find(
    (item) => item.slug === project.tool_type,
  )?.exports ?? ["txt"];

  return (
    <div className="grid gap-4">
      {error && (
        <p
          role="alert"
          className="rounded-lg border border-danger/40 bg-danger/10 px-4 py-3 text-sm text-danger"
        >
          {error}
        </p>
      )}

      <QualityBadge messages={messages} project={project} />

      <div className="flex flex-wrap items-center gap-2">
        <div
          role="group"
          aria-label={messages.result.compare}
          className="flex rounded-lg border border-border p-0.5"
        >
          {(["result", "original", "compare"] as View[]).map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={view === option}
              onClick={() => setView(option)}
              className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                view === option
                  ? "bg-accent text-accent-fg"
                  : "text-muted hover:text-fg"
              }`}
            >
              {option === "result"
                ? messages.result.translated
                : option === "original"
                  ? messages.result.original
                  : messages.result.compare}
            </button>
          ))}
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="btn-ghost px-2"
            onClick={() => void step(-1)}
            disabled={saving || historyIndex === 0}
            aria-label={messages.editor.undo}
            title={messages.editor.undo}
          >
            ↶
          </button>
          <button
            type="button"
            className="btn-ghost px-2"
            onClick={() => void step(1)}
            disabled={saving || historyIndex >= history.length}
            aria-label={messages.editor.redo}
            title={messages.editor.redo}
          >
            ↷
          </button>
          <button
            type="button"
            className="btn-ghost px-2"
            onClick={() => setZoom((z) => Math.max(0.25, z - 0.25))}
            aria-label={messages.editor.zoomOut}
          >
            −
          </button>
          <span className="text-xs tabular-nums text-muted">
            {Math.round(zoom * 100)}%
          </span>
          <button
            type="button"
            className="btn-ghost px-2"
            onClick={() => setZoom((z) => Math.min(4, z + 0.25))}
            aria-label={messages.editor.zoomIn}
          >
            +
          </button>
          <button
            type="button"
            className="btn-ghost"
            onClick={() => setZoom(1)}
          >
            {messages.editor.fit}
          </button>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <div className="card overflow-hidden">
          {view === "compare" ? (
            page?.original_url && (page.rendered_url ?? page.preview_url) ? (
              <BeforeAfterSlider
                beforeUrl={page.original_url}
                afterUrl={page.rendered_url ?? page.preview_url ?? ""}
                beforeLabel={messages.result.original}
                afterLabel={messages.result.translated}
                ariaLabel={messages.result.compare}
              />
            ) : (
              <div className="grid gap-px bg-border sm:grid-cols-2">
                <figure className="bg-surface p-2">
                  <figcaption className="mb-2 text-xs font-medium text-muted">
                    {messages.result.original}
                  </figcaption>
                  {page?.original_url && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={page.original_url}
                      alt={messages.result.original}
                      className="w-full"
                    />
                  )}
                </figure>
                <figure className="bg-surface p-2">
                  <figcaption className="mb-2 text-xs font-medium text-muted">
                    {messages.result.translated}
                  </figcaption>
                  {(page?.rendered_url ?? page?.preview_url) && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={page.rendered_url ?? page.preview_url ?? ""}
                      alt={messages.result.translated}
                      className="w-full"
                    />
                  )}
                </figure>
              </div>
            )
          ) : (
            <div className="grid gap-2 p-2" ref={imageWrapRef}>
              <div
                role="toolbar"
                aria-label={messages.editor.title}
                className="flex flex-wrap items-center gap-2"
              >
                <div className="flex rounded-lg border border-border p-0.5">
                  {(
                    [
                      ["select", "✥"],
                      ["polygon", "⬠"],
                      ["brush", "✎"],
                      ["eraser", "⌫"],
                    ] as [CanvasTool, string][]
                  ).map(([option, glyph]) => (
                    <button
                      key={option}
                      type="button"
                      aria-pressed={tool === option}
                      onClick={() => setTool(option)}
                      title={
                        option === "brush"
                          ? messages.editor.brush
                          : option === "eraser"
                            ? messages.editor.eraser
                            : option === "polygon"
                              ? messages.editor.polygon
                              : messages.editor.selectTool
                      }
                      className={`rounded-md px-2.5 py-1 text-sm ${
                        tool === option
                          ? "bg-accent text-accent-fg"
                          : "text-muted hover:text-fg"
                      }`}
                    >
                      <span aria-hidden>{glyph}</span>
                      <span className="sr-only">
                        {option === "brush"
                          ? messages.editor.brush
                          : option === "eraser"
                            ? messages.editor.eraser
                            : option === "polygon"
                              ? messages.editor.polygon
                              : messages.editor.selectTool}
                      </span>
                    </button>
                  ))}
                </div>

                {(tool === "brush" || tool === "eraser") && (
                  <label className="flex items-center gap-2 text-xs text-muted">
                    {messages.editor.brushSize}
                    <input
                      type="range"
                      min={4}
                      max={120}
                      value={brushSize}
                      onChange={(event) =>
                        setBrushSize(Number(event.target.value))
                      }
                    />
                  </label>
                )}

                <label className="flex items-center gap-1.5 text-xs text-muted">
                  <input
                    type="checkbox"
                    checked={showBoxes}
                    onChange={(event) => setShowBoxes(event.target.checked)}
                  />
                  {messages.editor.blocks}
                </label>
                <label className="flex items-center gap-1.5 text-xs text-muted">
                  <input
                    type="checkbox"
                    checked={showMask}
                    onChange={(event) => setShowMask(event.target.checked)}
                  />
                  {messages.editor.maskLayer}
                </label>
                {maskStrokes.length > 0 && (
                  <button
                    type="button"
                    className="btn-ghost text-xs"
                    onClick={() => setMaskStrokes([])}
                  >
                    {messages.common.cancel}
                  </button>
                )}
              </div>

              {page && imageUrl ? (
                <CanvasStage
                  page={page}
                  imageUrl={imageUrl}
                  regions={page.regions}
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                  onBoxChange={handleBoxChange}
                  onPolygonChange={handlePolygonChange}
                  onMaskChange={setMaskStrokes}
                  tool={tool}
                  layers={{
                    base: view === "original" ? "original" : "result",
                    boxes: showBoxes,
                    mask: showMask,
                  }}
                  zoom={zoom}
                  onZoomChange={setZoom}
                  brushSize={brushSize}
                  labels={{
                    canvas: messages.editor.title,
                    zoomIn: messages.editor.zoomIn,
                    zoomOut: messages.editor.zoomOut,
                    fit: messages.editor.fit,
                  }}
                />
              ) : (
                <div className="skeleton aspect-[4/3] w-full" />
              )}
            </div>
          )}

          {pages.length > 1 && (
            <div className="scroll-x flex gap-2 border-t border-border p-3">
              {pages.map((item, index) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => {
                    setPageIndex(index);
                    setSelectedId(null);
                  }}
                  aria-current={index === pageIndex}
                  className={`shrink-0 rounded-lg border p-1 ${
                    index === pageIndex ? "border-accent" : "border-border"
                  }`}
                >
                  {item.thumbnail_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={item.thumbnail_url}
                      alt=""
                      className="h-16 w-12 object-cover"
                    />
                  ) : (
                    <span className="grid h-16 w-12 place-items-center text-xs">
                      {item.page_number}
                    </span>
                  )}
                  <span className="mt-1 block text-center text-[10px] text-muted">
                    {item.page_number}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>

        <aside className="grid content-start gap-4">
          {selected ? (
            <BlockPanel
              messages={messages}
              region={selected}
              saving={saving}
              onChange={patchRegion}
              onRetranslate={() =>
                runAction(
                  `/api/v1/projects/${project.id}/regions/${selected.id}/retranslate`,
                )
              }
              onReocr={() =>
                runAction(
                  `/api/v1/projects/${project.id}/regions/${selected.id}/reocr`,
                )
              }
              onDelete={async () => {
                await apiFetch(
                  `/api/v1/projects/${project.id}/regions/${selected.id}`,
                  { method: "DELETE" },
                );
                setSelectedId(null);
                setDirty(true);
                await onReload();
              }}
            />
          ) : (
            <BlockList
              messages={messages}
              page={page}
              onSelect={setSelectedId}
            />
          )}

          <div className="card p-4">
            <h2 className="mb-3 text-sm font-semibold">
              {messages.result.download}
            </h2>
            {(dirty || maskStrokes.length > 0) && (
              <button
                type="button"
                className="btn-primary mb-3 w-full"
                disabled={busy}
                onClick={() => void applyEdits()}
              >
                {busy ? messages.editor.applying : messages.editor.apply}
              </button>
            )}
            <div className="grid grid-cols-2 gap-2">
              {toolExports.map((formatName) => (
                <button
                  key={formatName}
                  type="button"
                  className="btn-secondary text-xs"
                  disabled={busy}
                  onClick={() => download(formatName)}
                >
                  {formatName.replace("_", " ").toUpperCase()}
                </button>
              ))}
            </div>
            <button
              type="button"
              className="btn-ghost mt-3 w-full"
              onClick={async () => {
                await navigator.clipboard.writeText(plainText);
                setCopied(true);
                setTimeout(() => setCopied(false), 1600);
              }}
            >
              {copied ? messages.result.copied : messages.result.copy}
            </button>
            <button
              type="button"
              className="btn-ghost mt-1 w-full"
              onClick={onReset}
            >
              {messages.result.newFile}
            </button>
          </div>
        </aside>
      </div>
    </div>
  );
}

function BlockList({
  messages,
  page,
  onSelect,
}: {
  messages: Messages;
  page: PageResponse | undefined;
  onSelect: (id: string) => void;
}) {
  const regions = [...(page?.regions ?? [])].sort(
    (a, b) => a.reading_order - b.reading_order,
  );
  return (
    <div className="card p-4">
      <h2 className="mb-3 text-sm font-semibold">{messages.editor.blocks}</h2>
      {/* This list is also the accessible alternative to the image overlay. */}
      <ol className="max-h-96 space-y-1 overflow-y-auto text-sm">
        {regions.map((region) => (
          <li key={region.id}>
            <button
              type="button"
              onClick={() => onSelect(region.id)}
              className="w-full rounded-lg px-2 py-1.5 text-left hover:bg-raised"
            >
              <span className="line-clamp-2">
                {region.translated_text ??
                  region.normalized_text ??
                  region.source_text}
              </span>
              {(region.confidence ?? 1) < 0.75 && (
                <span className="mt-0.5 block text-[11px] text-warn">
                  {messages.editor.lowConfidence}
                </span>
              )}
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}

function BlockPanel({
  messages,
  region,
  saving,
  onChange,
  onRetranslate,
  onReocr,
  onDelete,
}: {
  messages: Messages;
  region: RegionResponse;
  saving: boolean;
  onChange: (
    region: RegionResponse,
    changes: Record<string, unknown>,
  ) => Promise<void>;
  onRetranslate: () => Promise<void>;
  onReocr: () => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  const [sourceText, setSourceText] = useState(
    region.normalized_text ?? region.source_text,
  );
  const [translatedText, setTranslatedText] = useState(
    region.translated_text ?? "",
  );

  useEffect(() => {
    setSourceText(region.normalized_text ?? region.source_text);
    setTranslatedText(region.translated_text ?? "");
  }, [
    region.id,
    region.normalized_text,
    region.source_text,
    region.translated_text,
  ]);

  const provenance = region.translation?.source as
    keyof Messages["editor"]["source"] | undefined;

  return (
    <div className="card p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold">{messages.editor.properties}</h2>
        <span className="chip">{region.region_type}</span>
      </div>

      <label className="label" htmlFor="source-text">
        {messages.editor.sourceText}
      </label>
      <textarea
        id="source-text"
        className="input min-h-20 resize-y"
        value={sourceText}
        onChange={(event) => setSourceText(event.target.value)}
        onBlur={() => {
          if (sourceText !== (region.normalized_text ?? region.source_text)) {
            void onChange(region, { source_text: sourceText });
          }
        }}
      />
      {region.confidence !== null && (
        <p className="mt-1 text-xs text-muted">
          {messages.result.quality.factors.ocr_confidence}:{" "}
          {Math.round(region.confidence * 100)}%
        </p>
      )}

      {region.translated_text !== null && (
        <>
          <label className="label mt-4" htmlFor="translated-text">
            {messages.editor.translatedText}
          </label>
          <textarea
            id="translated-text"
            className="input min-h-20 resize-y"
            value={translatedText}
            onChange={(event) => setTranslatedText(event.target.value)}
            onBlur={() => {
              if (translatedText !== (region.translated_text ?? "")) {
                void onChange(region, { translated_text: translatedText });
              }
            }}
          />
          {provenance && messages.editor.source[provenance] && (
            <p className="mt-1 text-xs text-muted">
              — {messages.editor.source[provenance]}
            </p>
          )}
        </>
      )}

      <div className="mt-4 grid gap-2">
        <button
          type="button"
          className="btn-secondary text-xs"
          onClick={() => void onRetranslate()}
        >
          {messages.editor.retranslate}
        </button>
        <button
          type="button"
          className="btn-secondary text-xs"
          onClick={() => void onReocr()}
        >
          {messages.editor.reocr}
        </button>
        <label className="flex items-center gap-2 text-xs text-muted">
          <input
            type="checkbox"
            checked={region.skip_translation}
            onChange={(event) =>
              void onChange(region, { skip_translation: event.target.checked })
            }
          />
          {messages.editor.skipTranslation}
        </label>
        <button
          type="button"
          className="btn-ghost text-xs text-danger"
          onClick={() => void onDelete()}
        >
          {messages.editor.delete}
        </button>
      </div>

      <p aria-live="polite" className="mt-3 text-xs text-muted">
        {saving ? messages.editor.saving : messages.editor.saved}
      </p>
    </div>
  );
}

function QualityBadge({
  messages,
  project,
}: {
  messages: Messages;
  project: ProjectResponse;
}) {
  if (!project.quality_band) return null;
  const band = project.quality_band as keyof Messages["result"]["quality"];
  const tone =
    project.quality_band === "high"
      ? "border-ok/40 bg-ok/10 text-ok"
      : project.quality_band === "medium"
        ? "border-border bg-raised text-muted"
        : "border-warn/40 bg-warn/10 text-warn";

  return (
    <details className={`rounded-card border px-4 py-3 text-sm ${tone}`}>
      <summary className="cursor-pointer font-semibold">
        {(messages.result.quality[band] as string) ?? project.quality_band}
      </summary>
      <p className="mt-2 text-xs opacity-90">
        {messages.result.quality.explain}
      </p>
      {project.quality_reasons.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs opacity-90">
          {project.quality_reasons.map((reason) => (
            <li key={reason.key}>
              {(messages.result.quality.factors as Record<string, string>)[
                reason.key
              ] ?? reason.key}
              : {Math.round(reason.score * 100)}%
              {reason.detail ? ` — ${reason.detail}` : ""}
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}
