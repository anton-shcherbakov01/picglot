/** @type {import('next').NextConfig} */
const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// Where the object store answers browsers — the origin presigned URLs point at,
// and a different origin from the site even when it is a subdomain of it. It has
// to be named here or every picture is blocked before a request is made, which
// looks nothing like a policy error: the store is healthy, the object is there,
// the logs are clean, and the canvas is empty. Say so at startup rather than
// leaving that to be discovered from a blank frame.
const storageUrl = (process.env.NEXT_PUBLIC_S3_URL ?? "").trim();
if (!storageUrl && process.env.NODE_ENV === "production") {
  console.warn(
    "[picglot] NEXT_PUBLIC_S3_URL is unset: images served straight from object " +
      "storage will be blocked by the Content-Security-Policy. Set it to the same " +
      "origin as S3_PUBLIC_ENDPOINT_URL (e.g. https://s3.example.com).",
  );
}

// The API and the object store are the only external origins we talk to;
// everything else is denied.
const csp = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'" +
    (process.env.NODE_ENV === "development" ? " 'unsafe-eval'" : ""),
  "style-src 'self' 'unsafe-inline'",
  ["img-src 'self' data: blob:", apiUrl, storageUrl].filter(Boolean).join(" "),
  "font-src 'self' data:",
  ["connect-src 'self'", apiUrl, storageUrl].filter(Boolean).join(" "),
  "media-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "upgrade-insecure-requests",
].join("; ");

const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  compress: true,
  productionBrowserSourceMaps: false,

  images: {
    formats: ["image/avif", "image/webp"],
    remotePatterns: [
      { protocol: "http", hostname: "localhost" },
      { protocol: "https", hostname: "**" },
    ],
  },

  experimental: {
    optimizePackageImports: [],
  },

  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: csp },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Permissions-Policy",
            value:
              "camera=(self), microphone=(), geolocation=(), payment=(self)",
          },
          { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
        ],
      },
      {
        // Private results must never be cached by an intermediary.
        source: "/:locale/app/:path*",
        headers: [
          { key: "Cache-Control", value: "private, no-store" },
          { key: "X-Robots-Tag", value: "noindex, nofollow" },
        ],
      },
      {
        source: "/share/:path*",
        headers: [
          { key: "Cache-Control", value: "private, no-store" },
          { key: "X-Robots-Tag", value: "noindex, nofollow" },
        ],
      },
    ];
  },

  async redirects() {
    return [
      // Legacy/duplicate paths collapse into the canonical locale-prefixed URL.
      {
        source: "/image-translator",
        destination: "/en/image-translator",
        permanent: true,
      },
      { source: "/pricing", destination: "/en/pricing", permanent: true },
      { source: "/api-docs", destination: "/en/api", permanent: true },
    ];
  },
};

export default nextConfig;
