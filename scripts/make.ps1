# Minimal PowerShell shim for Windows users without GNU make.
# Usage: .\scripts\make.ps1 <target>
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateSet('setup', 'dev', 'dev-api', 'dev-web', 'lint', 'typecheck', 'test',
                 'test-e2e', 'build', 'up', 'down', 'migrate', 'seed', 'health', 'fmt')]
    [string]$Target
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root '.venv\Scripts\python.exe'
Push-Location $root
try {
    switch ($Target) {
        'setup' {
            python -m venv .venv
            & $venvPython -m pip install --upgrade pip wheel
            & $venvPython -m pip install -e 'apps/api[dev,ocr]'
            npm install
            if (-not (Test-Path .env)) {
                Copy-Item .env.example .env
                Write-Host '-> created .env from .env.example'
            }
            Write-Host 'Setup complete. Next: .\scripts\make.ps1 up; .\scripts\make.ps1 migrate; .\scripts\make.ps1 seed'
        }
        'dev' {
            docker compose up -d postgres redis minio minio-init mailpit
            npm run dev
        }
        'dev-api' {
            & (Join-Path $root '.venv\Scripts\uvicorn.exe') lingoimage.main:app --reload --host 0.0.0.0 --port 8000
        }
        'dev-web' {
            npm run dev --workspace @lingoimage/web
        }
        'lint' {
            & $venvPython -m ruff check apps/api
            & $venvPython -m ruff format --check apps/api
            npm run lint
        }
        'typecheck' {
            & $venvPython -m mypy apps/api/lingoimage
            npm run typecheck
        }
        'fmt' {
            & $venvPython -m ruff format apps/api
            & $venvPython -m ruff check --fix apps/api
            npm run format
        }
        'test' {
            & $venvPython -m pytest apps/api/tests -v
            npm run test
        }
        'test-e2e' {
            npm run test:e2e
        }
        'build' {
            & $venvPython -m compileall -q apps/api/lingoimage
            npm run build
        }
        'up' {
            docker compose up -d --build
        }
        'down' {
            docker compose down
        }
        'migrate' {
            & (Join-Path $root '.venv\Scripts\alembic.exe') -c apps/api/alembic.ini upgrade head
        }
        'seed' {
            & $venvPython -m lingoimage.cli seed
        }
        'health' {
            & $venvPython -m lingoimage.cli health
        }
    }
}
finally {
    Pop-Location
}
