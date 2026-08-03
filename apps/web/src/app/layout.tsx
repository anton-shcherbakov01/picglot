import type { Metadata, Viewport } from "next";
import "./globals.css";

const BRAND = process.env.NEXT_PUBLIC_BRAND_NAME ?? "PicGlot";

export const metadata: Metadata = {
  title: { default: BRAND, template: `%s · ${BRAND}` },
  applicationName: BRAND,
  manifest: "/manifest.webmanifest",
  icons: {
    icon: [
      { url: "/favicon.svg", type: "image/svg+xml" },
      { url: "/favicon.ico", sizes: "32x32" },
    ],
    apple: "/icons/apple-touch-icon.png",
  },
  formatDetection: { telephone: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fafafb" },
    { media: "(prefers-color-scheme: dark)", color: "#0c0e12" },
  ],
};

/**
 * Applied before paint so a dark-mode user never sees a white flash.
 * Reads the stored preference, falling back to the OS setting.
 */
const THEME_BOOTSTRAP = `
(function () {
  try {
    var stored = localStorage.getItem('theme');
    var dark = stored === 'dark' ||
      (stored !== 'light' && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.classList.toggle('dark', dark);
  } catch (e) {}
})();`;

/** Registered here (not the locale layout) because this is the only layout that owns <body>. */
const SW_REGISTER = `
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/sw.js').catch(function () {});
  });
}`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
      </head>
      <body className="min-h-dvh antialiased">
        {children}
        <script dangerouslySetInnerHTML={{ __html: SW_REGISTER }} />
      </body>
    </html>
  );
}
