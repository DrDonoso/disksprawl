import { defineConfig } from '@playwright/test'
import { existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

// Browser tests of the real application (`npm run build`, then `npm run e2e`): Playwright starts
// scripts/smoke_server.py from the repository's .venv, with a fresh database and a synthetic sample
// tree, and the tests drive the built frontend in dist/. Set E2E_BASE_URL to test another server
// (an isolated, empty one: the tests add directories and scan them).
const windows = process.platform === 'win32'
const python = resolve('..', '.venv', windows ? 'Scripts' : 'bin', windows ? 'python.exe' : 'python')
const port = Number(process.env.E2E_PORT || '7889')
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('E2E_PORT must be a valid port')
const localOrigin = `http://127.0.0.1:${port}`
if (!process.env.E2E_BASE_URL && !existsSync(resolve('dist', 'index.html'))) throw new Error('Build the app first: npm run build')
/** Where smoke_server.py creates the sample tree (the tests change it between scans). */
process.env.E2E_SAMPLE_ROOT ??= join(tmpdir(), `disksprawl-e2e-${port}`, 'data', 'storage')
process.env.E2E_PYTHON ??= python

export default defineConfig({
  testDir: './e2e',
  testIgnore: /demo\.spec\.ts$/,
  outputDir: './test-results/app',
  workers: 1,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  forbidOnly: Boolean(process.env.CI),
  use: {
    baseURL: process.env.E2E_BASE_URL || localOrigin,
    channel: process.env.PLAYWRIGHT_CHANNEL,
    colorScheme: 'light',
    locale: 'en-GB',
    trace: 'retain-on-failure',
  },
  webServer: process.env.E2E_BASE_URL ? undefined : {
    command: `"${python}" "${resolve('..', 'scripts', 'smoke_server.py')}" --port ${port}`,
    url: `${localOrigin}/health`,
    reuseExistingServer: false,
    timeout: 90_000,
  },
})
