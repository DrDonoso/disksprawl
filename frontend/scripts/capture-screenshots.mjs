/**
 * Captures the screenshots of README.md (docs/screenshots/*.png) from the static demo and its
 * synthetic data:
 *
 *   npm run build:demo
 *   npm run screenshots
 *
 * Environment variables:
 * - SCREENSHOT_BASE_URL: the demo to capture, http://127.0.0.1:4183 by default. When nothing answers
 *   there, the script serves dist-demo with `vite preview --mode demo` and stops it at the end.
 * - SCREENSHOT_OUT_DIR: where the PNGs go, docs/screenshots by default.
 * - SCREENSHOT_ONLY: comma-separated names to capture only some, e.g. `overview,mobile`.
 *
 * Every screenshot starts a fresh browser (each page load starts the demo over), in English with
 * the light theme, at 1440×900 or on a 390×844 phone, with the demo banner hidden. The clock is
 * fixed at 17:30 UTC of the day of the capture, so the texts do not depend on when the script runs.
 * Console errors and /api requests the demo does not answer make it fail.
 *
 * Optimize the PNGs afterwards (lossless), e.g. with Docker:
 *   docker run --rm -v "<repository>/docs/screenshots:/work" ghcr.io/shssoichiro/oxipng -o 4 --strip safe --recursive /work
 */
import { chromium } from '@playwright/test'
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const baseUrl = new URL(process.env.SCREENSHOT_BASE_URL || 'http://127.0.0.1:4183')
const outDir = resolve(process.env.SCREENSHOT_OUT_DIR || resolve(frontend, '../docs/screenshots'))
const only = (process.env.SCREENSHOT_ONLY || '').split(',').map(name => name.trim()).filter(Boolean)
const today = new Date().toISOString().slice(0, 10)
const CLOCK = new Date(`${today}T17:30:00Z`).getTime()
const DESKTOP = { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 }
const PHONE = { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true }
const HIDE_BANNER = 'aside[aria-label="About this demo"] { display: none !important; }'

/** Starts a scan of the directory shown and moves the clock to about half of it. */
async function scan(page) {
  await page.getByRole('button', { name: 'Scan now' }).click()
  await page.getByRole('region', { name: 'Scan in progress' }).waitFor()
  await page.clock.setFixedTime(CLOCK + 3200)
  await page.getByText(/[1-9]\d*% done/).waitFor({ timeout: 15_000 })
}

const section = (page, name) => page.locator('section', { has: page.getByRole('heading', { name, exact: true }) }).first()

/** The screenshots, in the order of the README; `capture` brings the page to the state to show. */
const SHOTS = [
  {
    name: 'overview',
    path: '/directories/1',
    async capture(page) {
      await page.getByRole('group', { name: 'Treemap of Media' }).getByRole('button').first().waitFor()
    },
  },
  {
    name: 'changes',
    path: '/directories/1?view=changes',
    async capture(page) {
      const compare = page.getByRole('combobox', { name: 'Compare with' })
      await compare.locator('option').nth(1).waitFor({ state: 'attached' })
      await compare.selectOption({ index: await compare.locator('option').count() - 1 })
      await page.getByText(/^From .* to /).waitFor()
      await page.getByRole('heading', { name: 'Biggest changes' }).waitFor()
      await section(page, 'Contents').evaluate(element => window.scrollBy(0, element.getBoundingClientRect().top - 64))
    },
  },
  {
    name: 'history',
    path: '/directories/3',
    async capture(page) {
      await page.getByRole('heading', { name: 'Latest scans' }).waitFor()
      return section(page, 'History')
    },
  },
  {
    name: 'settings',
    path: '/directories/1?settings=1',
    async capture(page) {
      const dialog = page.getByRole('dialog', { name: 'Settings' })
      await dialog.getByRole('radio', { name: /gdu/ }).waitFor()
      const tip = dialog.getByRole('button', { name: 'About gdu' })
      await tip.scrollIntoViewIfNeeded()
      await tip.hover()
      await page.getByRole('tooltip').waitFor()
    },
  },
  {
    name: 'folder-picker',
    path: '/directories/1?settings=new',
    async capture(page) {
      const picker = page.getByRole('dialog', { name: 'Settings' }).getByRole('group', { name: 'Folders' })
      await picker.getByRole('button', { name: 'downloads' }).click()
      await picker.getByRole('button', { name: 'torrents' }).waitFor()
    },
  },
  {
    name: 'scanning',
    path: '/directories/2',
    capture: scan,
  },
  {
    name: 'sidebar',
    path: '/directories/2',
    device: { ...DESKTOP, deviceScaleFactor: 2 },
    clip: { x: 0, y: 0, width: 720, height: 450 },
    capture: scan,
  },
  {
    name: 'mobile',
    path: '/directories/1',
    device: PHONE,
    async capture(page) {
      await page.getByRole('group', { name: 'Treemap of Media' }).getByRole('button').first().waitFor()
    },
  },
]

async function reachable(url) {
  try {
    await fetch(url)
    return true
  } catch {
    return false
  }
}

async function serve() {
  if (await reachable(baseUrl)) return null
  if (!['127.0.0.1', 'localhost'].includes(baseUrl.hostname)) throw new Error(`${baseUrl} does not answer`)
  if (!existsSync(resolve(frontend, 'dist-demo', 'index.html'))) throw new Error('Build the demo first: npm run build:demo')
  const server = spawn('npx', ['vite', 'preview', '--mode', 'demo', '--host', baseUrl.hostname, '--port', baseUrl.port || '80', '--strictPort'], {
    cwd: frontend, stdio: 'ignore', shell: process.platform === 'win32',
  })
  for (let attempt = 0; attempt < 60; attempt++) {
    if (await reachable(baseUrl)) return server
    await new Promise(done => setTimeout(done, 500))
  }
  server.kill()
  throw new Error('The demo preview did not start')
}

async function capture(browser, shot) {
  const context = await browser.newContext({
    ...(shot.device ?? DESKTOP), locale: 'en-GB', timezoneId: 'Etc/UTC', colorScheme: 'light', serviceWorkers: 'allow',
  })
  const page = await context.newPage()
  const problems = []
  page.on('pageerror', error => problems.push(`uncaught: ${error.message}`))
  page.on('console', message => { if (message.type() === 'error') problems.push(message.text()) })
  await page.clock.setFixedTime(CLOCK)
  await page.goto(new URL(shot.path, baseUrl).toString())
  await page.addStyleTag({ content: HIDE_BANNER })
  await page.getByRole('button', { name: 'Explore' }).click()
  await page.getByRole('dialog', { name: 'Welcome to the DiskSprawl demo' }).waitFor({ state: 'hidden' })
  await page.mouse.move(0, 0)
  const target = await shot.capture(page)
  await page.waitForTimeout(300)
  if (problems.length) throw new Error(`${shot.name}: ${problems.join(' | ')}`)
  const file = resolve(outDir, `${shot.name}.png`)
  if (target) await target.screenshot({ path: file })
  else await page.screenshot({ path: file, clip: shot.clip })
  console.log(`Wrote ${file}`)
  await context.close()
}

mkdirSync(outDir, { recursive: true })
const server = await serve()
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL })
try {
  for (const shot of SHOTS) {
    if (!only.length || only.includes(shot.name)) await capture(browser, shot)
  }
} finally {
  await browser.close()
  server?.kill()
}
