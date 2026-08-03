# syntax=docker/dockerfile:1.7
# Next.js standalone build.
FROM node:22-alpine AS deps
WORKDIR /app
RUN apk add --no-cache libc6-compat
COPY package.json package-lock.json* ./
COPY apps/web/package.json ./apps/web/
# `packages/*` is declared in the root workspaces glob but no package exists
# yet; copying them individually made the build fail on a path that was never
# there. Add a COPY line here when a real package appears.
#
# The root `prepare` script runs during `npm ci`, so the file it invokes has to
# be in the layer or the install dies on MODULE_NOT_FOUND. It is copied on its
# own rather than the whole scripts/ directory to keep this layer's cache from
# being invalidated by unrelated scripts. `--ignore-scripts` would have been
# the shorter fix but sharp, esbuild and unrs-resolver need their install
# scripts to place native binaries.
COPY scripts/install-hooks.mjs ./scripts/
RUN npm ci --workspaces --include-workspace-root

FROM node:22-alpine AS builder
WORKDIR /app
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_SITE_URL=http://localhost:3000
ARG NEXT_PUBLIC_BRAND_NAME="PicGlot"
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_SITE_URL=$NEXT_PUBLIC_SITE_URL \
    NEXT_PUBLIC_BRAND_NAME=$NEXT_PUBLIC_BRAND_NAME \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY --from=deps /app/apps/web/node_modules ./apps/web/node_modules
COPY . .
RUN npm run build --workspace @picglot/web

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs
COPY --from=builder --chown=nextjs:nodejs /app/apps/web/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/apps/web/.next/static ./apps/web/.next/static
COPY --from=builder --chown=nextjs:nodejs /app/apps/web/public ./apps/web/public
USER nextjs
EXPOSE 3000
STOPSIGNAL SIGTERM
CMD ["node", "apps/web/server.js"]
