/**
 * Product analytics.
 *
 * Only bounded, non-identifying properties are ever sent — the server drops
 * anything outside its allow-list as well, so document content cannot leak
 * even if a caller here gets it wrong.
 */

import { API_URL } from "./api";

const ANON_KEY = "picglot_anon_id";
const UTM_KEY = "picglot_first_touch";

function anonymousId(): string | undefined {
  if (typeof window === "undefined") return undefined;
  try {
    let id = localStorage.getItem(ANON_KEY);
    if (!id) {
      id = crypto.randomUUID();
      localStorage.setItem(ANON_KEY, id);
    }
    return id;
  } catch {
    return undefined;
  }
}

/** Record the first landing page and campaign; later touches never overwrite it. */
export function captureFirstTouch(): Record<string, string> {
  if (typeof window === "undefined") return {};
  try {
    const existing = localStorage.getItem(UTM_KEY);
    if (existing) return JSON.parse(existing) as Record<string, string>;

    const params = new URLSearchParams(window.location.search);
    const touch: Record<string, string> = {
      landing_path: window.location.pathname,
      referrer: document.referrer ? new URL(document.referrer).hostname : "",
    };
    for (const key of [
      "utm_source",
      "utm_medium",
      "utm_campaign",
      "utm_term",
      "utm_content",
    ]) {
      const value = params.get(key);
      if (value) touch[key] = value.slice(0, 120);
    }
    localStorage.setItem(UTM_KEY, JSON.stringify(touch));
    return touch;
  } catch {
    return {};
  }
}

function consented(): boolean {
  if (typeof window === "undefined") return false;
  try {
    // Default is opt-out until the visitor answers the consent prompt.
    return localStorage.getItem("analytics_consent") === "granted";
  } catch {
    return false;
  }
}

export function track(
  name: string,
  properties: Record<string, unknown> = {},
): void {
  if (typeof window === "undefined" || !consented()) return;

  const payload = JSON.stringify({
    name,
    properties,
    anonymous_id: anonymousId(),
    locale: document.documentElement.lang || undefined,
    utm: captureFirstTouch(),
  });

  const url = `${API_URL}/api/v1/analytics/events`;
  // sendBeacon survives navigation away from the page.
  if (navigator.sendBeacon) {
    navigator.sendBeacon(
      url,
      new Blob([payload], { type: "application/json" }),
    );
    return;
  }
  void fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: payload,
    credentials: "include",
    keepalive: true,
  }).catch(() => undefined);
}

export function setConsent(granted: boolean): void {
  try {
    localStorage.setItem("analytics_consent", granted ? "granted" : "denied");
  } catch {
    /* private mode */
  }
}

export function consentState(): "granted" | "denied" | "unknown" {
  if (typeof window === "undefined") return "unknown";
  try {
    const value = localStorage.getItem("analytics_consent");
    return value === "granted" || value === "denied" ? value : "unknown";
  } catch {
    return "unknown";
  }
}
