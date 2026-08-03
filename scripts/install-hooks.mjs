#!/usr/bin/env node
/**
 * Installs the pre-commit hook. Silently does nothing outside a git checkout
 * (CI, Docker builds, tarball installs) so `npm install` never fails there.
 */
import { existsSync, mkdirSync, writeFileSync, chmodSync } from "node:fs";
import { join } from "node:path";

const gitDir = join(process.cwd(), ".git");
if (!existsSync(gitDir)) {
  console.log("[hooks] not a git checkout — skipping");
  process.exit(0);
}

const hooksDir = join(gitDir, "hooks");
mkdirSync(hooksDir, { recursive: true });

const hook = `#!/bin/sh
# Managed by scripts/install-hooks.mjs — edit that file, not this one.
set -e

staged=$(git diff --cached --name-only --diff-filter=ACM)
[ -z "$staged" ] && exit 0

py=$(echo "$staged" | grep -E '\\.py$' || true)
ts=$(echo "$staged" | grep -E '\\.(ts|tsx|js|mjs)$' || true)

if [ -n "$py" ] && [ -x .venv/bin/ruff ]; then
  echo "[pre-commit] ruff"
  .venv/bin/ruff check $py
  .venv/bin/ruff format --check $py
fi

if [ -n "$ts" ] && [ -x node_modules/.bin/prettier ]; then
  echo "[pre-commit] prettier"
  node_modules/.bin/prettier --check $ts
fi

# Cheap secret sweep: catches the obvious paste-a-key-into-a-file mistake.
#
# Only added lines are scanned, and the generator that defines these patterns
# is excluded: otherwise the whole diff matches hunk headers and context, and
# editing the generator flags itself on every commit.
if git diff --cached --unified=0 -- . ':(exclude)scripts/install-hooks.mjs' |
  grep -E '^\\+' |
  grep -vE '^\\+\\+\\+ ' |
  grep -nE '(sk_live_|whsec_[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----)'; then
  echo "[pre-commit] refusing: a credential appears to be staged" >&2
  exit 1
fi
`;

const path = join(hooksDir, "pre-commit");
writeFileSync(path, hook, { mode: 0o755 });
try {
  chmodSync(path, 0o755);
} catch {
  /* Windows */
}
console.log("[hooks] pre-commit installed");
