"use client";

import Konva from "konva";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  const [size, setSize] = useState({ width: 800, height: 600 });
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [strokes, setStrokes] = useState<MaskStroke[]>([]);
  const [drawing, setDrawing] = useState(false);
  const pinchRef = useRef<{ distance: number; zoom: number } | null>(null);

  // ------------------------------------------------------------------ image
  useEffect(() => {
    if (!imageUrl) {
      setImage(null);
      return;
    }
    const element = new window.Image();
    element.crossOrigin = "anonymous";
    let cancelled = false;
    element.onload = () => {
      if (!cancelled) setImage(element);
    };
    element.src = imageUrl;
    return () => {
      cancelled = true;
    };
  }, [imageUrl]);

  // Track the container so the stage fills it and stays responsive.
  useEffect(() => {
    const node = containerRef.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => {
      if (!entry) return;
      setSize({
        width: Math.max(200, entry.contentRect.width),
        height: Math.max(240, Math.min(720, entry.contentRect.width * 0.75)),
      });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

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
  const handleWheel = (event: Konva.KonvaEventObject<WheelEvent>) => {
    event.evt.preventDefault();
    const stage = stageRef.current;
    if (!stage) return;
    const pointer = stage.getPointerPosition();
    if (!pointer) return;

    const direction = event.evt.deltaY > 0 ? -1 : 1;
    const next = Math.min(
      8,
      Math.max(0.05, zoom * (direction > 0 ? 1.1 : 1 / 1.1)),
    );
    // Keep the point under the cursor stationary while scaling.
    const world = {
      x: (pointer.x - offset.x) / zoom,
      y: (pointer.y - offset.y) / zoom,
    };
    setOffset({ x: pointer.x - world.x * next, y: pointer.y - world.y * next });
    onZoomChange(next);
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
      onZoomChange(Math.min(8, Math.max(0.05, pinchRef.current.zoom * ratio)));
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
  }, [zoom, onZoomChange]);

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
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="btn-ghost text-xs"
          onClick={() => onZoomChange(Math.max(0.05, zoom / 1.2))}
          aria-label={labels.zoomOut}
        >
          −
        </button>
        <span className="text-xs tabular-nums text-muted">
          {Math.round(zoom * 100)}%
        </span>
        <button
          type="button"
          className="btn-ghost text-xs"
          onClick={() => onZoomChange(Math.min(8, zoom * 1.2))}
          aria-label={labels.zoomIn}
        >
          +
        </button>
        <button type="button" className="btn-ghost text-xs" onClick={fit}>
          {labels.fit}
        </button>
      </div>

      <div
        ref={containerRef}
        className="overflow-hidden rounded-card border border-border bg-raised"
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
