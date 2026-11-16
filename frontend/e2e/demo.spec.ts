import AxeBuilder from '@axe-core/playwright'
import { expect, test as base, type Page } from '@playwright/test'
import { en } from '../src/i18n/locales/en'
import { es } from '../src/i18n/locales/es'
import type { Dictionary, Messages } from '../src/i18n/types'

type Texts = Messages<Dictionary>

// The public demo (playwright.demo.config.ts): the static build with its in-browser API. Besides
// each test's checks, nothing may go wrong unnoticed: no /api request may reach the static host,
// no API answer may be a server error (the catch-all's 501 included) and no console error may happen.

const UNHANDLED_PREFIX = '[demo] Unhandled API request:'

interface Problems {
  escaped: string[]
  serverErrors: string[]
  errors: string[]
}

const test = base.extend<{ problems: Problems }>({
  problems: [async ({ page }, use) => {
    const problems: Problems = { escaped: [], serverErrors: [], errors: [] }
    page.on('pageerror', error => problems.errors.push(`uncaught: ${error.message}`))
    page.on('console', message => {
      if (message.type() !== 'error') return
      const text = message.text()
      if (!text.startsWith('Failed to load resource') || text.startsWith(UNHANDLED_PREFIX)) problems.errors.push(text)
    })
    page.on('response', response => {
      if (!new URL(response.url()).pathname.startsWith('/api/')) return
      const label = `${response.request().method()} ${new URL(response.url()).pathname} → ${response.status()}`
      if (!response.fromServiceWorker()) problems.escaped.push(label)
      if (response.status() >= 500) problems.serverErrors.push(label)
    })
    await use(problems)
    expect(problems.escaped, 'API requests that reached the static host').toEqual([])
    expect(problems.serverErrors, 'API answers with a server error').toEqual([])
    expect(problems.errors, 'uncaught errors and console errors').toEqual([])
  }, { auto: true }],
})

async function expectAccessible(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
  expect(results.violations.map(violation => `${violation.id}: ${violation.nodes.map(node => node.target.join(' ')).join(', ')}`)).toEqual([])
}

/** A message with its one placeholder filled, such as "Language: {language}". */
function fill(template: string, value: string): string {
  return template.replace(/\{\w+\}/, value)
}

/** Whether a CSS color is dark: below the midpoint in every channel. */
function isDark(color: string): boolean {
  const channels = color.match(/\d+(\.\d+)?/g)?.slice(0, 3).map(Number) ?? []
  return channels.length === 3 && channels.every(channel => channel < 128)
}

async function enter(page: Page, path = '/', messages: Texts = en): Promise<void> {
  const response = await page.goto(path)
  if (response) expect(response.status()).toBe(200)
  const welcome = page.getByRole('dialog', { name: messages.demo.welcome.title })
  await expect(welcome).toBeVisible()
  await welcome.getByRole('button', { name: messages.demo.welcome.explore }).click()
  await expect(welcome).toBeHidden()
}

test('opens with the welcome dialog, the banner and the static host\'s security headers', async ({ page }) => {
  const response = await page.goto('/')
  expect(response?.headers()['content-security-policy']).toContain("worker-src 'self'")
  expect(response?.headers()['x-frame-options']).toBe('DENY')
  await expect(page).toHaveTitle(/DiskSprawl/)
  await expect(page.getByRole('dialog', { name: en.demo.welcome.title })).toContainText(en.demo.welcome.data)
  await expectAccessible(page)
  await page.getByRole('button', { name: en.demo.welcome.explore }).click()
  await expect(page.getByRole('complementary', { name: en.demo.banner.label })).toContainText(en.demo.banner.text)
  await expect(page).toHaveURL(/\/directories\/1$/)
  await expect(page.getByRole('heading', { level: 1, name: 'Media' })).toBeVisible()
  // Media is scanned daily at 03:00: how long ago depends on the time of day.
  await expect(page.getByRole('main').getByText(/^Updated (a few seconds ago|\d+ (seconds?|minutes?|hours?) ago|yesterday)$/)).toBeVisible()
})

for (const [language, messages] of [['en', en], ['es', es]] as [string, Texts][]) {
  test(`every screen works and is accessible (${language})`, async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem('disksprawl_language', value), language)
    await enter(page, '/', messages)
    const nav = page.getByRole('navigation', { name: messages.sidebar.label })
    for (const name of ['Media', 'Photos', 'Backups', 'Documents']) {
      await nav.getByRole('link', { name: new RegExp(`^${name}`) }).click()
      await expect(page.getByRole('heading', { level: 1, name })).toBeVisible()
      await expect(page.getByRole('heading', { name: messages.history.title })).toBeVisible()
      await expectAccessible(page)
    }
    await expect(page.getByRole('main')).toContainText(messages.directory.attempt.failed.withData.split('{')[0].trim())

    await nav.getByRole('link', { name: /^Media/ }).click()
    await page.getByRole('radio', { name: messages.treemap.changes }).click()
    await expect(page.getByRole('heading', { name: messages.treemap.biggest })).toBeVisible()
    await expect(page.getByRole('group', { name: /Media/ }).getByRole('button').first()).toBeVisible()
    await expectAccessible(page)

    await page.getByRole('button', { name: messages.shell.settings }).click()
    const dialog = page.getByRole('dialog', { name: messages.settings.title })
    await expectAccessible(page)
    await dialog.getByRole('button', { name: new RegExp(`^${messages.common.edit}`) }).first().click()
    await expect(dialog.getByRole('radio', { name: /gdu/ })).toBeChecked()
    await expectAccessible(page)
    await dialog.getByRole('tab', { name: messages.settings.tabs.general }).click()
    await expectAccessible(page)
  })
}

test('"Scan now" shows the progress and then the new data', async ({ page }) => {
  await enter(page, '/directories/2')
  await page.getByRole('button', { name: 'Scan now' }).click()
  const progress = page.getByRole('region', { name: en.progress.label })
  await expect(progress).toBeVisible()
  await expect(progress.getByRole('progressbar')).toBeVisible()
  await expect(progress).toContainText(/\d+% done/)
  await expect(page.getByRole('navigation', { name: en.sidebar.label }).getByRole('link', { name: /^Photos/ })).toContainText(/Scanning/)
  await expect(progress).toBeHidden({ timeout: 20_000 })
  await expect(page.getByRole('main').getByText('Updated a few seconds ago')).toBeVisible()
})

test('a directory can be added, edited and deleted', async ({ page }) => {
  await enter(page, '/?settings=new')
  const dialog = page.getByRole('dialog', { name: en.settings.title })
  await dialog.getByRole('group', { name: en.picker.label }).getByRole('button', { name: 'vm-images' }).click()
  await dialog.getByRole('button', { name: en.picker.choose }).click()
  await dialog.getByRole('button', { name: en.form.addSubmit }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'vm-images' })).toBeVisible()
  await expect(page.getByText(en.directory.emptyTitle).first()).toBeVisible()
  await page.getByRole('button', { name: 'Scan now' }).click()
  await expect(page.getByRole('main').getByText('Updated a few seconds ago')).toBeVisible({ timeout: 20_000 })
  await page.getByRole('button', { name: en.shell.settings }).click()
  await dialog.getByRole('button', { name: 'Edit vm-images' }).click()
  await dialog.getByRole('button', { name: en.common.delete }).click()
  await dialog.getByRole('button', { name: en.form.deleteSubmit }).click()
  await expect(page.getByRole('navigation', { name: en.sidebar.label }).getByRole('link', { name: /vm-images/ })).toHaveCount(0)
})

test('on a phone the menu covers the page and closes after choosing', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await enter(page)
  const nav = page.getByRole('navigation', { name: en.sidebar.label })
  await expect(nav).toBeHidden()
  await page.getByRole('button', { name: en.shell.toggleMenu }).click()
  await nav.getByRole('link', { name: /^Backups/ }).click()
  await expect(nav).toBeHidden()
  await expect(page.getByRole('heading', { level: 1, name: 'Backups' })).toBeVisible()
  await expectAccessible(page)

  // The language and theme menus fit in the bar, next to the settings.
  const bar = page.getByRole('banner')
  const theme = bar.getByRole('button', { name: fill(en.shell.theme, en.shell.themes.auto) })
  for (const button of [bar.getByRole('button', { name: fill(en.shell.language, 'English') }), theme,
    bar.getByRole('button', { name: en.shell.settings })]) {
    await expect(button).toBeInViewport({ ratio: 1 })
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await theme.click()
  await expectAccessible(page)
  await page.getByRole('menuitemradio', { name: en.shell.themes.dark }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
})

test.describe('in a Spanish browser', () => {
  test.use({ locale: 'es-ES' })

  test('the demo is in English until Spanish is chosen, and the browser keeps the language and the theme', async ({ page }) => {
    await enter(page)
    await expect(page.locator('html')).toHaveAttribute('lang', 'en')
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
    await page.getByRole('button', { name: fill(en.shell.language, 'English') }).click()
    await page.getByRole('menuitemradio', { name: 'Español' }).click()
    await expect(page.locator('html')).toHaveAttribute('lang', 'es')
    await expect(page.getByRole('heading', { name: es.history.title })).toBeVisible()

    // The system is light (playwright.demo.config.ts); the dark theme is chosen.
    await page.getByRole('button', { name: fill(es.shell.theme, es.shell.themes.auto) }).click()
    await expect(page.getByRole('menuitemradio', { name: es.shell.themes.auto })).toBeChecked()
    await expectAccessible(page)
    await page.getByRole('menuitemradio', { name: es.shell.themes.dark }).click()
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
    const tile = page.getByRole('group', { name: /Media/ }).getByRole('button').first()
    await expect(tile).toBeVisible()
    const colors = await page.evaluate(() => ({
      page: getComputedStyle(document.body).backgroundColor,
      scheme: getComputedStyle(document.documentElement).colorScheme,
    }))
    expect(isDark(colors.page), colors.page).toBe(true)
    expect(colors.scheme).toBe('dark')
    const tileColor = await tile.evaluate(element => getComputedStyle(element).backgroundColor)
    expect(isDark(tileColor), tileColor).toBe(true)
    await expectAccessible(page)

    await page.reload()
    await expect(page.getByRole('dialog', { name: es.demo.welcome.title })).toBeVisible()
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
    await expectAccessible(page)
  })
})

test('wide screens: the page fills the window, without scrolling sideways', async ({ page }) => {
  for (const width of [1920, 2560, 3440]) {
    await page.setViewportSize({ width, height: 1440 })
    await enter(page, '/directories/1')
    const treemap = page.getByRole('group', { name: 'Treemap of Media' })
    await expect(treemap.getByRole('button').first()).toBeVisible()
    await expect(page.getByRole('heading', { name: en.history.recent })).toBeVisible()
    const layout = await page.evaluate(() => {
      const box = (selector: string) => {
        const rect = document.querySelector(selector)?.getBoundingClientRect()
        return rect ? { left: rect.left, right: rect.right, width: rect.width } : null
      }
      return {
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
        menu: box('#directory-list'),
        main: box('main'),
        treemap: box('[role="group"][aria-label^="Treemap"]'),
        table: box('[role="region"][aria-label^="Folders"]'),
        charts: box('figure'),
        scans: box('[role="region"][aria-labelledby="history-recent"]'),
      }
    })
    expect(layout.scrollWidth, `${width}: horizontal scrolling`).toBeLessThanOrEqual(layout.clientWidth)
    // The content takes everything right of the menu: no maximum width.
    expect(layout.main?.left).toBeCloseTo(layout.menu?.right ?? -1, 0)
    expect(layout.main?.right).toBeCloseTo(layout.clientWidth, 0)
    // The treemap grows with the window, with its table beside it, and the latest scans beside the charts.
    expect(layout.treemap?.width ?? 0).toBeGreaterThan(0.6 * (layout.main?.width ?? 0))
    expect(layout.table?.left ?? 0).toBeGreaterThan(layout.treemap?.right ?? Infinity)
    expect(layout.scans?.left ?? 0).toBeGreaterThan(layout.charts?.right ?? Infinity)
  }
  await page.getByRole('radio', { name: en.treemap.changes }).click()
  await expect(page.getByRole('heading', { name: en.treemap.biggest })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0)
  await expectAccessible(page)
})
