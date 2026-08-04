import type { Config } from "tailwindcss";

/**
 * Type is a local stack on purpose.
 *
 * A downloaded webfont would be fetched at build time, which makes every
 * production build depend on a third party being up — not a trade worth making
 * for a self-hosted tool. Each platform's best face is named explicitly so we
 * get Segoe UI Variable, SF Pro or Roboto rather than whatever `sans-serif`
 * happens to resolve to, and the character comes from the scale, tracking and
 * colour instead.
 */
const SANS = [
  "Inter var",
  "Inter",
  "SF Pro Text",
  "-apple-system",
  "BlinkMacSystemFont",
  "Segoe UI Variable Text",
  "Segoe UI",
  "Roboto",
  "Helvetica Neue",
  "Arial",
  "sans-serif",
];

const config: Config = {
  darkMode: "class",
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "rgb(var(--bg) / <alpha-value>)",
        surface: "rgb(var(--surface) / <alpha-value>)",
        raised: "rgb(var(--raised) / <alpha-value>)",
        border: "rgb(var(--border) / <alpha-value>)",
        fg: "rgb(var(--fg) / <alpha-value>)",
        muted: "rgb(var(--muted) / <alpha-value>)",
        accent: "rgb(var(--accent) / <alpha-value>)",
        "accent-fg": "rgb(var(--accent-fg) / <alpha-value>)",
        "accent-soft": "rgb(var(--accent-soft) / <alpha-value>)",
        mint: "rgb(var(--mint) / <alpha-value>)",
        ok: "rgb(var(--ok) / <alpha-value>)",
        warn: "rgb(var(--warn) / <alpha-value>)",
        danger: "rgb(var(--danger) / <alpha-value>)",
      },
      fontFamily: {
        sans: SANS,
        display: ["SF Pro Display", "Segoe UI Variable Display", ...SANS],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "JetBrains Mono",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
      fontSize: {
        // Display sizes carry their own leading and tracking so headings never
        // depend on someone remembering to add both utilities.
        display: [
          "clamp(2.1rem, 1.35rem + 3.4vw, 3.75rem)",
          {
            lineHeight: "1.04",
            letterSpacing: "-0.035em",
            fontWeight: "800",
          },
        ],
        title: [
          "clamp(1.6rem, 1.2rem + 1.6vw, 2.25rem)",
          {
            lineHeight: "1.12",
            letterSpacing: "-0.025em",
            fontWeight: "700",
          },
        ],
      },
      borderRadius: {
        card: "16px",
        panel: "20px",
      },
      maxWidth: {
        content: "1180px",
        prose: "68ch",
      },
      boxShadow: {
        soft: "0 1px 2px rgb(var(--shadow) / 0.05), 0 10px 30px -18px rgb(var(--shadow) / 0.35)",
        lift: "0 2px 4px rgb(var(--shadow) / 0.06), 0 18px 48px -24px rgb(var(--shadow) / 0.45)",
        // A single hairline of light along the top edge; reads as a physical
        // bevel and is what keeps flat cards from looking like plain divs.
        edge: "inset 0 1px 0 rgb(255 255 255 / 0.06)",
      },
      keyframes: {
        "fade-up": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "none" },
        },
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
        "sheet-in": {
          from: { opacity: "0", transform: "translateY(-8px)" },
          to: { opacity: "1", transform: "none" },
        },
      },
      animation: {
        "fade-up": "fade-up 260ms cubic-bezier(0.22, 1, 0.36, 1) both",
        shimmer: "shimmer 1.6s infinite",
        "sheet-in": "sheet-in 160ms cubic-bezier(0.22, 1, 0.36, 1) both",
      },
    },
  },
  plugins: [],
};

export default config;
