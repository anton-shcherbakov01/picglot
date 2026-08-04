/**
 * The PicGlot mark: two overlapping plates — a picture and, in front of it, a
 * translated one. No `"use client"`: the header is a client component and the
 * footer is a server one, and both draw this.
 */
export function Wordmark({ size = 32 }: { size?: number }) {
  return (
    <span
      aria-hidden
      className="grid shrink-0 place-items-center rounded-[10px] bg-accent text-accent-fg shadow-soft"
      style={{ width: size, height: size }}
    >
      <svg
        width={size * 0.56}
        height={size * 0.56}
        viewBox="0 0 20 20"
        fill="none"
      >
        <rect
          x="1.6"
          y="4.4"
          width="12"
          height="11"
          rx="2.4"
          stroke="currentColor"
          strokeWidth="1.6"
          opacity="0.55"
        />
        <rect
          x="6.4"
          y="1.6"
          width="12"
          height="11"
          rx="2.4"
          fill="currentColor"
          opacity="0.18"
        />
        <rect
          x="6.4"
          y="1.6"
          width="12"
          height="11"
          rx="2.4"
          stroke="currentColor"
          strokeWidth="1.6"
        />
        <path
          d="M9.4 8.4h6M12.4 5.6v5.6"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
      </svg>
    </span>
  );
}
