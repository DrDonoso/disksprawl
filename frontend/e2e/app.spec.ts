import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'

// The real application with a synthetic sample tree (playwright.config.ts, scripts/smoke_server.py).

async function expectAccessible(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
  expect(results.violations.map(violation => `${violation.id}: ${violation.nodes.map(node => node.target.join(' ')).join(', ')}`)).toEqual([])
}

function changeSampleTree(step: number): void {
  const python = process.env.E2E_PYTHON
  const root = process.env.E2E_SAMPLE_ROOT
  if (!python || !root) throw new Error('E2E_PYTHON and E2E_SAMPLE_ROOT are set by playwright.config.ts')
  execFileSync(python, [resolve('..', 'scripts', 'make_sample_tree.py'), root, '--mutate', String(step)])
}

test('add a directory, scan it, explore the treemap and see what changed', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Welcome to DiskSprawl' })).toBeVisible()
  await expectAccessible(page)

  await page.getByRole('main').getByRole('button', { name: 'Add a directory' }).click()
  const dialog = page.getByRole('dialog', { name: 'Settings' })
  await expect(dialog.getByRole('heading', { name: 'Add a directory' })).toBeVisible()
  await dialog.getByRole('group', { name: 'Folders' }).getByRole('button', { name: 'storage' }).click()
  await dialog.getByRole('button', { name: 'Use this folder' }).click()
  await dialog.getByLabel('Name').fill('Storage')
  await dialog.getByText('Maximum', { exact: true }).click()
  await expect(dialog.getByRole('radio', { name: 'Maximum' })).toBeChecked()
  await expect(dialog.getByText(/^Next runs \(server time/)).toBeVisible()
  await expectAccessible(page)
  await dialog.getByRole('button', { name: 'Add directory' }).click()

  await expect(page).toHaveURL(/\/directories\/\d+$/)
  await expect(page.getByRole('heading', { level: 1, name: 'Storage' })).toBeVisible()
  await expect(page.getByRole('main').getByText('Not scanned yet').first()).toBeVisible()
  await page.getByRole('button', { name: 'Scan now' }).click()
  await expect(page.getByRole('main').getByText('Updated a few seconds ago')).toBeVisible({ timeout: 60_000 })
  await expect(page.getByRole('navigation', { name: 'Directories' }).getByRole('link', { name: /Storage/ })).toContainText('Updated')

  const treemap = page.getByRole('group', { name: 'Treemap of Storage' })
  await expect(treemap.getByRole('button', { name: /^Backups \d/ })).toBeVisible()
  await treemap.getByRole('button', { name: /^Media \d/ }).click()
  await expect(page.getByRole('navigation', { name: 'Folder path' }).getByText('Media')).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('group', { name: 'Treemap of Media' }).getByRole('button', { name: /^Series \d/ })).toBeVisible()
  await expectAccessible(page)
  await page.getByRole('navigation', { name: 'Folder path' }).getByRole('button', { name: 'Storage' }).click()

  changeSampleTree(1)
  await page.getByRole('button', { name: 'Scan now' }).click()
  const changes = page.getByRole('radio', { name: 'Changes' })
  await expect(changes).toBeEnabled({ timeout: 60_000 })
  await changes.click()
  await expect(page.getByText(/^From .+ to .+$/)).toBeVisible()
  // The emptied recycle bin is still there, as deleted, and the new season shows up as a change.
  await expect(page.getByRole('row', { name: /#recycle .* deleted/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Biggest changes' })).toBeVisible()
  await expect(page.getByRole('listitem').filter({ hasText: /^Media\/Series\/.+ new$/ }).first()).toBeVisible()
  await expectAccessible(page)
  await expect(page.getByRole('table').last().getByRole('row')).toHaveCount(3)
  expect(errors).toEqual([])
})

test('settings explain every option, and the menu remembers being hidden', async ({ page, request, baseURL }) => {
  const added = await request.post('/api/directories', {
    data: { name: 'Backups', path: `${process.env.E2E_SAMPLE_ROOT}/Backups`, engine: 'native' },
    headers: { Origin: new URL(baseURL ?? '').origin, 'X-DiskSprawl-Request': '1' },
  })
  expect([201, 409]).toContain(added.status())
  await page.goto('/')
  await page.getByRole('button', { name: 'Settings' }).click()
  const dialog = page.getByRole('dialog', { name: 'Settings' })
  await dialog.getByRole('button', { name: /^Edit / }).first().click()
  const tip = dialog.getByRole('button', { name: 'About gdu' })
  await tip.hover()
  await expect(page.getByRole('tooltip')).toContainText('8 threads at most')
  await expectAccessible(page)
  await dialog.getByRole('tab', { name: 'General' }).click()
  await expect(dialog.getByLabel('Detailed scans kept per directory', { exact: true })).toHaveValue('30')
  // The language is chosen in the top bar only.
  await expect(dialog.getByLabel('Language', { exact: true })).toHaveCount(0)
  await expectAccessible(page)
  await dialog.getByRole('button', { name: 'Close' }).click()

  const toggle = page.getByRole('button', { name: 'Show or hide the directories' })
  await toggle.click()
  await expect(page.getByRole('navigation', { name: 'Directories' })).toBeHidden()
  await page.reload()
  await expect(page.getByRole('navigation', { name: 'Directories' })).toBeHidden()
  await page.getByRole('button', { name: 'Show or hide the directories' }).click()
  await expect(page.getByRole('navigation', { name: 'Directories' })).toBeVisible()
})

test.describe('in a Spanish browser', () => {
  test.use({ locale: 'es-ES' })

  test('the interface is in English until Spanish is chosen; the browser keeps the language and the theme', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('html')).toHaveAttribute('lang', 'en')
    await expect(page.getByRole('button', { name: 'Settings' })).toBeVisible()
    await page.getByRole('button', { name: 'Language: English' }).click()
    await page.getByRole('menuitemradio', { name: 'Español' }).click()
    await expect(page.getByRole('button', { name: 'Ajustes' })).toBeVisible()
    await expect(page.locator('html')).toHaveAttribute('lang', 'es')
    await page.getByRole('button', { name: 'Tema: Automático (sistema)' }).click()
    await page.getByRole('menuitemradio', { name: 'Oscuro' }).click()
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
    await expectAccessible(page)

    await page.reload()
    await expect(page.getByRole('button', { name: 'Tema: Oscuro' })).toBeVisible()
    await expect(page.locator('html')).toHaveAttribute('lang', 'es')

    // The theme is in place before the application's code runs: here, without it at all.
    await page.route(/\/assets\/index-[\w-]+\.js$/, route => route.abort())
    await page.reload()
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
    expect(await page.evaluate(() => ({
      background: getComputedStyle(document.body).backgroundColor,
      scheme: getComputedStyle(document.documentElement).colorScheme,
      browserColor: document.querySelector('meta[name="theme-color"][data-theme="dark"]')?.getAttribute('media'),
    }))).toEqual({ background: 'rgb(20, 23, 28)', scheme: 'dark', browserColor: 'all' })
    await expect(page.getByRole('button', { name: 'Ajustes' })).toHaveCount(0)
  })
})

test('on a very wide screen the page fills the window, without scrolling sideways', async ({ page }) => {
  await page.setViewportSize({ width: 3440, height: 1440 })
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  const layout = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
    main: document.querySelector('main')?.getBoundingClientRect().right,
  }))
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.clientWidth)
  expect(layout.main).toBeCloseTo(layout.clientWidth, 0)
  await expectAccessible(page)
})
