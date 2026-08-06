/**
 * Liveness for the web container.
 *
 * The compose healthcheck has always fetched this path and nothing has ever
 * served it, so the container reported `unhealthy` from its first run to its
 * last — a permanent red light that says nothing about whether the site is up,
 * which is worse than no healthcheck at all: it is the one signal an operator
 * looks at, and it was lying.
 *
 * It answers for the Node process only. Whether the API, the database or the
 * object store are reachable is what `/health/ready` on the API is for, and a
 * web tier that marks itself unhealthy because a dependency is down takes
 * itself out of rotation over something it cannot fix.
 */

export const dynamic = "force-dynamic";

export function GET() {
  return Response.json({ status: "ok" }, { status: 200 });
}
