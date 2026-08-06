import { describe, expect, it } from "vitest";

import { GET } from "@/app/api/healthz/route";

/**
 * The compose healthcheck fetches this path. It fetched it for the whole life
 * of the deployment while nothing served it, and the container reported
 * `unhealthy` the entire time — so the route existing is the thing worth a
 * test, more than what it returns.
 */
describe("web liveness", () => {
  it("answers 200 so the container can report itself healthy", async () => {
    const response = GET();
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ status: "ok" });
  });
});
