"use client";

import Konva from "konva";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Circle,
  Image as KonvaImage,
  Layer,
  Line,
  Rect,
  Stage,
  Transformer,
} from "react-konva";

import type { PageResponse, RegionResponse } from "@/lib/api";

export type CanvasTool = "select" | "polygon" | "brush" | "eraser";

export interface BoxChange {
  x: number;
  y: number;
  width: number;
  height: number;
  rotation: number;
}

export interface CanvasLayers {
  /** Which underlying raster the stage shows. */
  base: "result" | "original" | "cleaned";
  boxes: boolean;
  mask: boolean;
}

interface Props {
  page: PageResponse;
  imageUrl: string | null | undefined;
  regions: RegionResponse[];
  selectedId: string | null;
  onSelect: (regionId: string | null) => void;
  onBoxChange: (region: RegionResponse, box: BoxChange) => void;
  onPolygonChange: (region: RegionResponse, polygon: number[][]) => void;
  onMaskChange: (strokes: MaskStroke[]) => void;
  tool: CanvasTool;
  layers: CanvasLayers;
  zoom: number;
  onZoomChange: (zoom: number) => void;
  brushSize: number;
  labels: {
    canvas: string;
    zoomIn: string;
    zoomOut: string;
    fit: string;
    imageFailed: string;
  };
}

export interface MaskStroke {
  points: number[];
  size: number;
  erase: boolean;
}

/** Distance between two touch points, for pinch-zoom. */
function touchDistance(a: Touch, b: Touch): number {
  return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
}

/**
 * Konva stage for pointer work: pan, zoom, move/resize/rotate a block, drag
 * polygon vertices, paint an inpainting mask.
 *
 * It is deliberately *not* the only way to edit. The block list beside it stays
 * the keyboard and screen-reader path, so the canvas can stay a pointer
 * surface without having to reimplement an accessibility tree on top of a
 * bitmap. Geometry changes are handed upward rather than written here — the
 * parent owns versioning, undo and persistence.
 */
export function CanvasStage({
  page,
  imageUrl,
  regions,
  selectedId,
  onSelect,
  onBoxChange,
  onPolygonChange,
  onMaskChange,
  tool,
  layers,
  zoom,
  onZoomChange,
  brushSize,
  labels,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<Konva.Stage>(null);
  const transformerRef = useRef<Konva.Transformer>(null);
  const selectedShapeRef = useRef<Konva.Rect>(null);

  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const [imageFailed, setImageFailed] = useState(false);
  const [size, setSize] = useState({ width: 320, height: 240 });
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [strokes, setStrokes] = useState<MaskStroke[]>([]);
  const [drawing, setDrawing] = useState(false);
  const pinchRef = useRef<{ distance: number; zoom: number } | null>(null);

  // ------------------------------------------------------------------ image
  useEffect(() => {
    if (!imageUrl) {
      setImage(null);
      setImageFailed(false);
      return;
    }
    let cancelled = false;
    setImageFailed(false);

    const load = (anonymous: boolean) =>
      new Promise<HTMLImageElement>((resolve, reject) => {
        const element = new window.Image();
        // Setting crossOrigin makes the browser *require* CORS headers rather
        // than merely prefer them, so this attempt fails outright against an
        // object store that answers without them.
        if (anonymous) element.crossOrigin = "anonymous";
        element.onload = () => resolve(element);
        element.onerror = () => reject(new Error("image load failed"));
        element.src = imageUrl;
      });

    // Pages come from storage on a separate host through presigned URLs. Try
    // for an untainted canvas first, then settle for a tainted one: nothing
    // here reads pixels back, and a picture the user cannot see is a far worse
    // outcome than a canvas we cannot export from. A blank stage with the
    // blocks drawn on top of nothing is what this fallback exists to prevent.
    void load(true)
      .catch(() => load(false))
      .then((element) => {
        if (!cancelled) setImage(element);
      })
      .catch(() => {
        if (cancelled) return;
        setImage(null);
        setImageFailed(true);
      });

    return () => {
      cancelled = true;
    };
  }, [imageUrl]);

  // The frame takes the page's own proportions. A fixed 4:3 box leaves a band
  // of empty canvas above and below anything wider, and down either side of
  // anything taller — dark, unusable, and easily read as the picture having
  // failed to draw. The clamp keeps a panorama from collapsing to a strip and a
  // long portrait from pushing the controls off the screen.
  const pageAspect =
    page.width > 0 && page.height > 0 ? page.height / page.width : 0.75;

  // Track the container so the stage fills it and stays responsive. Measured
  // before paint, not after: the stage carries a fixed pixel width, so a frame
  // rendered at the placeholder size is a frame laid out several hundred pixels
  // wider than the phone it is on.
  useLayoutEffect(() => {
    const node = containerRef.current;
    if (!node) return;
    const apply = (width: number) => {
      if (width <= 0) return;
      setSize({
        width,
        height: Math.max(240, Math.min(720, Math.round(width * pageAspect))),
      });
    };
    apply(node.getBoundingClientRect().width);
    const observer = new ResizeObserver(([entry]) => {
      if (entry) apply(entry.contentRect.width);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [pageAspect]);

  const fit = useCallback(() => {
    if (!page.width || !page.height) return;
    const next = Math.min(size.width / page.width, size.height / page.height);
    onZoomChange(Number.isFinite(next) && next > 0 ? next : 1);
    setOffset({
      x: (size.width - page.width * next) / 2,
      y: (size.height - page.height * next) / 2,
    });
  }, [page.width, page.height, size, onZoomChange]);

  // Fit once the page or the container size is known.
  useEffect(() => {
    fit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page.id, size.width, size.height]);

  // Attach the transformer to whichever rectangle is selected.
  useEffect(() => {
    const transformer = transformerRef.current;
    const shape = selectedShapeRef.current;
    if (!transformer) return;
    transformer.nodes(shape && tool === "select" ? [shape] : []);
    transformer.getLayer()?.batchDraw();
  }, [selectedId, tool, regions]);

  const selected = useMemo(
    () => regions.find((region) => region.id === selectedId) ?? null,
    [regions, selectedId],
  );

  // ------------------------------------------------------------------ zoom
  /**
   * Scale around a fixed point, defaulting to the middle of the frame.
   *
   * Changing the scale without moving the origin with it drags the picture out
   * of view: two taps on the zoom buttons and the page sits half outside the
   * frame with dark canvas where it used to be. The wheel already anchored to
   * the cursor; the buttons and the pinch gesture did not, which is most of the
   * drift a phone can produce.
   */
  const zoomTo = useCallback(
    (next: number, anchor?: { x: number; y: number }) => {
      const clamped = Math.min(8, Math.max(0.05, next));
      if (clamped === zoom) return;
      const point = anchor ?? { x: size.width / 2, y: size.height / 2 };
      const ratio = clamped / zoom;
      setOffset({
        x: point.x - (point.x - offset.x) * ratio,
        y: point.y - (point.y - offset.y) * ratio,
      });
      onZoomChange(clamped);
    },
    [zoom, offset, size.width, size.height, onZoomChange],
  );

  const handleWheel = (event: Konva.KonvaEventObject<WheelEvent>) => {
    event.evt.preventDefault();
    const pointer = stageRef.current?.getPointerPosition();
    if (!pointer) return;
    const direction = event.evt.deltaY > 0 ? -1 : 1;
    zoomTo(zoom * (direction > 0 ? 1.1 : 1 / 1.1), pointer);
  };

  useEffect(() => {
    const node = containerRef.current;
    if (!node) return;
    const onTouchMove = (event: TouchEvent) => {
      if (event.touches.length !== 2) return;
      const [first, second] = [event.touches[0]!, event.touches[1]!];
      const distance = touchDistance(first, second);
      if (!pinchRef.current) {
        pinchRef.current = { distance, zoom };
        return;
      }
      const ratio = distance / pinchRef.current.distance;
      // Anchored between the fingers, so the picture stays where it is being
      // held rather than sliding off under them.
      const rect = node.getBoundingClientRect();
      zoomTo(pinchRef.current.zoom * ratio, {
        x: (first.clientX + second.clientX) / 2 - rect.left,
        y: (first.clientY + second.clientY) / 2 - rect.top,
      });
      event.preventDefault();
    };
    const onTouchEnd = () => {
      pinchRef.current = null;
    };
    node.addEventListener("touchmove", onTouchMove, { passive: false });
    node.addEventListener("touchend", onTouchEnd);
    return () => {
      node.removeEventListener("touchmove", onTouchMove);
      node.removeEventListener("touchend", onTouchEnd);
    };
  }, [zoom, zoomTo]);

  // ------------------------------------------------------------------ mask
  const pointerInImage = (): { x: number; y: number } | null => {
    const stage = stageRef.current;
    const pointer = stage?.getPointerPosition();
    if (!pointer) return null;
    return {
      x: (pointer.x - offset.x) / zoom,
      y: (pointer.y - offset.y) / zoom,
    };
  };

  const startStroke = () => {
    if (tool !== "brush" && tool !== "eraser") return;
    const point = pointerInImage();
    if (!point) return;
    setDrawing(true);
    setStrokes((current) => [
      ...current,
      { points: [point.x, point.y], size: brushSize, erase: tool === "eraser" },
    ]);
  };

  const extendStroke = () => {
    if (!drawing) return;
    const point = pointerInImage();
    if (!point) return;
    setStrokes((current) => {
      const last = current[current.length - 1];
      if (!last) return current;
      const updated = { ...last, points: [...last.points, point.x, point.y] };
      return [...current.slice(0, -1), updated];
    });
  };

  const endStroke = () => {
    if (!drawing) return;
    setDrawing(false);
    onMaskChange(strokes);
  };

  const panning = tool === "select" && !selectedId;

  return (
    <div className="relative min-w-0">
      {/* Zoom lives here, next to the surface it scales, and nowhere else. */}
      <div className="mb-2 flex flex-wrap items-center gap-1">
        <div className="flex items-center rounded-xl border border-border bg-surface p-0.5">
          <button
            type="button"
            className="btn-ghost h-8 px-2.5 text-base"
            onClick={() => zoomTo(zoom / 1.2)}
            aria-label={labels.zoomOut}
          >
            −
          </button>
          <span className="w-12 text-center text-xs font-medium tabular-nums text-muted">
            {Math.round(zoom * 100)}%
          </span>
          <button
            type="button"
            className="btn-ghost h-8 px-2.5 text-base"
            onClick={() => zoomTo(zoom * 1.2)}
            aria-label={labels.zoomIn}
          >
            +
          </button>
        </div>
        <button type="button" className="btn-ghost h-9 text-xs" onClick={fit}>
          {labels.fit}
        </button>
      </div>

      {imageFailed && (
        <p
          role="status"
          className="mb-2 rounded-xl border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn"
        >
          {labels.imageFailed}
        </p>
      )}

      <div
        ref={containerRef}
        className="dotfield relative overflow-hidden rounded-card border border-border bg-raised"
        // Pinch and drag belong to the stage; letting the browser also treat
        // them as page gestures makes both feel broken on a phone.
        style={{ touchAction: "none" }}
        // The canvas is a pointer convenience; the block list is the
        // accessible equivalent, so the stage itself is hidden from the
        // accessibility tree rather than exposing a meaningless bitmap.
        aria-hidden
      >
        <Stage
          ref={stageRef}
          width={size.width}
          height={size.height}
          onWheel={handleWheel}
          draggable={panning}
          x={offset.x}
          y={offset.y}
          scaleX={zoom}
          scaleY={zoom}
          onDragEnd={(event) => {
            if (!panning) return;
            setOffset({ x: event.target.x(), y: event.target.y() });
          }}
          onMouseDown={(event) => {
            if (tool === "brush" || tool === "eraser") {
              startStroke();
              return;
            }
            if (event.target === event.target.getStage()) onSelect(null);
          }}
          onMouseMove={extendStroke}
          onMouseUp={endStroke}
          onTouchStart={(event) => {
            if (tool === "brush" || tool === "eraser") startStroke();
            else if (event.target === event.target.getStage()) onSelect(null);
          }}
          onTouchMove={extendStroke}
          onTouchEnd={endStroke}
        >
          <Layer listening={false}>
            {image && (
              <KonvaImage
                image={image}
                width={page.width}
                height={page.height}
              />
            )}
          </Layer>

          {layers.mask && (
            <Layer listening={false} opacity={0.45}>
              {strokes.map((stroke, index) => (
                <Line
                  key={index}
                  points={stroke.points}
                  stroke="#38bdf8"
                  strokeWidth={stroke.size}
                  lineCap="round"
                  lineJoin="round"
                  globalCompositeOperation={
                    stroke.erase ? "destination-out" : "source-over"
                  }
                />
              ))}
            </Layer>
          )}

          {layers.boxes && (
            <Layer>
              {regions.map((region) => {
                const box = region.bounding_box;
                const isSelected = region.id === selectedId;
                return (
                  <Rect
                    key={region.id}
                    ref={isSelected ? selectedShapeRef : undefined}
                    x={box.x}
                    y={box.y}
                    width={box.width}
                    height={box.height}
                    rotation={region.rotation ?? 0}
                    stroke={isSelected ? "#6366f1" : "#94a3b8"}
                    strokeWidth={(isSelected ? 2 : 1) / zoom}
                    fill={
                      isSelected
                        ? "rgba(99,102,241,0.12)"
                        : "rgba(148,163,184,0.06)"
                    }
                    draggable={tool === "select"}
                    onClick={() => onSelect(region.id)}
                    onTap={() => onSelect(region.id)}
                    onDragEnd={(event) =>
                      onBoxChange(region, {
                        x: event.target.x(),
                        y: event.target.y(),
                        width: box.width,
                        height: box.height,
                        rotation: event.target.rotation(),
                      })
                    }
                    onTransformEnd={(event) => {
                      const node = event.target as Konva.Rect;
                      // Konva scales rather than resizing; fold the scale back
                      // into width/height so the stored box stays meaningful.
                      const scaleX = node.scaleX();
                      const scaleY = node.scaleY();
                      node.scaleX(1);
                      node.scaleY(1);
                      onBoxChange(region, {
                        x: node.x(),
                        y: node.y(),
                        width: Math.max(4, node.width() * scaleX),
                        height: Math.max(4, node.height() * scaleY),
                        rotation: node.rotation(),
                      });
                    }}
                  />
                );
              })}
              {tool === "select" && (
                <Transformer
                  ref={transformerRef}
                  rotateEnabled
                  keepRatio={false}
                  boundBoxFunc={(previous, next) =>
                    next.width < 8 || next.height < 8 ? previous : next
                  }
                />
              )}
            </Layer>
          )}

          {tool === "polygon" && selected && (
            <Layer>
              <Line
                points={selected.polygon.flat()}
                closed
                stroke="#f59e0b"
                strokeWidth={2 / zoom}
              />
              {selected.polygon.map((vertex, index) => (
                <Circle
                  key={index}
                  x={vertex[0]}
                  y={vertex[1]}
                  radius={5 / zoom}
                  fill="#f59e0b"
                  draggable
                  onDragEnd={(event) => {
                    const next = selected.polygon.map((point, at) =>
                      at === index
                        ? [event.target.x(), event.target.y()]
                        : point,
                    );
                    onPolygonChange(selected, next);
                  }}
                />
              ))}
            </Layer>
          )}
        </Stage>
      </div>
    </div>
  );
}
