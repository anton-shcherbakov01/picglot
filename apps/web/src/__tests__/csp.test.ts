import { describe, expect, it, vi } from "vitest";

/**
 * The Content-Security-Policy names every origin the browser is allowed to load
 * from, and the object store is a separate origin from the site even when it is
 * a subdomain of it. Leaving it out blocks every picture before a request is
 * made — which looks like nothing at all from the server: the store is healthy,
 * the object is there, the presigned URL returns 200, the logs are clean and
 * the canvas is empty.
 */
async function policy(env: Record<string, string>) {
  const previous = { ...process.env };
  Object.assign(process.env, env);
  try {
    // Re-imported per case: the policy is built when the module is evaluated.
    vi.resetModules();
    // next.config.mjs is plain JavaScript and ships no type declarations.
    // @ts-expect-error untyped module
    const loaded = await import("../../next.config.mjs");
    const config = loaded as {
      default: {
        headers: () => Promise<{ headers: { key: string; value: string }[] }[]>;
      };
    };
    const [route] = await config.default.headers();
    const header = route?.headers.find(
      (entry) => entry.key === "Content-Security-Policy",
    );
    if (!header) throw new Error("no Content-Security-Policy header");

    const parsed = new Map<string, string[]>(
      header.value.split("; ").map((directive) => {
        const [name, ...sources] = directive.split(" ");
        return [name ?? "", sources];
      }),
    );
    return (name: string): string[] => {
      const sources = parsed.get(name);
      if (!sources) throw new Error(`no ${name} directive in: ${header.value}`);
      return sources;
    };
  } finally {
    process.env = previous;
  }
}

const CONFIGURED = {
  NODE_ENV: "production",
  NEXT_PUBLIC_API_URL: "https://picglot.ru",
  NEXT_PUBLIC_S3_URL: "https://s3.picglot.ru",
};

describe("content security policy", () => {
  it("lets the browser load images and data from the object store", async () => {
    const sources = await policy(CONFIGURED);

    expect(sources("img-src")).toContain("https://s3.picglot.ru");
    expect(sources("connect-src")).toContain("https://s3.picglot.ru");
  });

  it("keeps the directives well formed when no store origin is configured", async () => {
    const sources = await policy({ ...CONFIGURED, NEXT_PUBLIC_S3_URL: "" });

    // An empty source would otherwise leave a trailing space in the directive.
    expect(sources("img-src").every(Boolean)).toBe(true);
    expect(sources("connect-src").every(Boolean)).toBe(true);
  });

  it("never widens the policy beyond the two origins we control", async () => {
    const sources = await policy(CONFIGURED);

    expect(sources("img-src")).not.toContain("*");
    expect(sources("img-src")).not.toContain("https:");
    expect(sources("default-src")).toEqual(["'self'"]);
  });
});
