#!/usr/bin/env node
/** Cross-platform `make setup` for contributors without GNU make. */
import { execSync } from 'node:child_process';
import { copyFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const root = process.cwd();
const isWindows = process.platform === 'win32';
const venvPython = join(root, '.venv', isWindows ? 'Scripts/python.exe' : 'bin/python');

function run(command) {
  console.log(`\n$ ${command}`);
  execSync(command, { stdio: 'inherit', cwd: root });
}

if (!existsSync(join(root, '.env'))) {
  copyFileSync(join(root, '.env.example'), join(root, '.env'));
  console.log('-> created .env from .env.example');
}

if (!existsSync(venvPython)) {
  run(`${isWindows ? 'python' : 'python3'} -m venv .venv`);
}

run(`"${venvPython}" -m pip install --upgrade pip wheel --quiet`);
run(`"${venvPython}" -m pip install -e "apps/api[dev,ocr]"`);
run('npm install --workspaces --include-workspace-root');

console.log(`
Setup complete.

  1. Start infrastructure:   docker compose up -d postgres redis minio mailpit
  2. Apply migrations:       "${venvPython}" -m alembic -c apps/api/alembic.ini upgrade head
  3. Seed data:              "${venvPython}" -m lingoimage.cli seed
  4. Check everything:       "${venvPython}" -m lingoimage.cli health
  5. Run the app:            npm run dev
`);
