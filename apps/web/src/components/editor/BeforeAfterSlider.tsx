"use client";

import { useCallback, useRef, useState } from "react";

/**
 * Before/after wipe.
 *
 * The handle is a real range input rather than a drag surface, so it works
 * with a keyboard and a screen reader for free and needs no pointer-event
 * bookkeeping of its own.
 */
export function BeforeAfterSlider({
  beforeUrl,
  afterUrl,
  beforeLabel,
  afterLabel,
  ariaLabel,
}: {
  beforeUrl: string;
  afterUrl: string;
  beforeLabel: string;
  afterLabel: string;
  ariaLabel: string;
}) {
  const [position, setPosition] = useState(50);
  const frameRef = useRef<HTMLDivElement>(null);

  const dragTo = useCallback((clientX: number) => {
    const frame = frameRef.current;
    if (!frame) return;
    const rect = frame.getBoundingClientRect();
    const next = ((clientX - rect.left) / rect.width) * 100;
    setPosition(Math.min(100, Math.max(0, next)));
  }, []);

  return (
    <div className="grid gap-2 p-2">
      <div
        ref={frameRef}
        className="relative select-none overflow-hidden rounded-lg"
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          dragTo(event.clientX);
        }}
        onPointerMove={(event) => {
          if (event.buttons === 1) dragTo(event.clientX);
        }}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={afterUrl}
          alt={afterLabel}
          className="block w-full"
          draggable={false}
        />
        {/* The two images are stacked at identical size and the top one is
            clipped, rather than sized against a measured container: a width
            read from the DOM is unknown on the first paint, which left the
            original squeezed into a corner until something re-rendered it. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={beforeUrl}
          alt={beforeLabel}
          draggable={false}
          className="absolute inset-0 block h-full w-full object-cover"
          style={{ clipPath: `inset(0 ${100 - position}% 0 0)` }}
        />
        <div
          aria-hidden
          className="pointer-events-none absolute inset-y-0 w-0.5 bg-accent"
          style={{ left: `${position}%` }}
        />
      </div>

      <label className="grid gap-1 text-xs text-muted">
        <span className="flex justify-between">
          <span>{beforeLabel}</span>
          <span>{afterLabel}</span>
        </span>
        <input
          type="range"
          min={0}
          max={100}
          value={position}
          aria-label={ariaLabel}
          onChange={(event) => setPosition(Number(event.target.value))}
        />
      </label>
    </div>
  );
}
