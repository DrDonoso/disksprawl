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
})
