import type { Locale } from "./i18n";

export function formatBytes(bytes: number, locale: Locale = "en"): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(
    units.length - 1,
    Math.floor(Math.log(bytes) / Math.log(1024)),
  );
  const value = bytes / 1024 ** index;
  return `${new Intl.NumberFormat(locale, {
    maximumFractionDigits: value < 10 && index > 0 ? 1 : 0,
  }).format(value)} ${units[index]}`;
}

export function formatMoney(
  minor: number,
  currency: string,
  locale: Locale = "en",
): string {
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
    minimumFractionDigits: minor % 100 === 0 ? 0 : 2,
  }).format(minor / 100);
}

export function formatNumber(value: number, locale: Locale = "en"): string {
  return new Intl.NumberFormat(locale).format(value);
}

export function formatDate(iso: string, locale: Locale = "en"): string {
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(
    new Date(iso),
  );
}

export function formatRelative(iso: string, locale: Locale = "en"): string {
  const target = new Date(iso).getTime();
  const deltaSeconds = Math.round((target - Date.now()) / 1000);
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ["year", 31_536_000],
    ["month", 2_592_000],
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60],
  ];
  const formatter = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  for (const [unit, seconds] of units) {
    if (Math.abs(deltaSeconds) >= seconds) {
      return formatter.format(Math.round(deltaSeconds / seconds), unit);
    }
  }
  return formatter.format(deltaSeconds, "second");
}

/** Bucketed file size for analytics — never the exact byte count. */
export function sizeBucket(bytes: number): string {
  if (bytes < 512 * 1024) return "<0.5MB";
  if (bytes < 2 * 1024 * 1024) return "0.5-2MB";
  if (bytes < 10 * 1024 * 1024) return "2-10MB";
  if (bytes < 50 * 1024 * 1024) return "10-50MB";
  return ">50MB";
}
