# Contributing

## Setup

```bash
cp .env.example .env
make setup        # Python venv + npm install
make up            # Docker infrastructure
make migrate && make seed
make dev            # infra in Docker, app processes locally with hot reload
```

Windows without GNU make: `.\scripts\make.ps1 <target>` or the equivalent
`npm run <target>` scripts — see `README.md`.

## Code style

* Python: `ruff check` / `ruff format` (see `apps/api/pyproject.toml`), mypy
  strict where the codebase already is.
* TypeScript: `eslint` + `tsc --noEmit`, `strict` and
  `noUncheckedIndexedAccess` in `apps/web/tsconfig.json`.
* Run `make lint && make typecheck && make test` before pushing — all three
  are also CI gates in `.github/workflows/ci.yml`.

## Commits

[Conventional Commits](https://www.conventionalcommits.org/):
`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.

## Pull requests

1. Branch from `main`.
2. Add or update tests for any behaviour change — this project's tests run
   the real pipeline (SQLite, local storage, inline queue), not mocks; new
   code should be testable the same way.
3. Ensure CI passes.
4. Describe what changed and why in the PR description, not in code
   comments.

## Architecture decisions

A significant, hard-to-reverse choice gets a numbered ADR in
`docs/architecture-decisions/` rather than being justified only in a commit
message.

## Security

Do not open a public issue for a vulnerability — see `SECURITY.md`.
