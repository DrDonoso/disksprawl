import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// The notifications of this browser, through the real server (scripts/smoke_server.py): its service
// worker, ⚙ Settings → Notifications, the registration and the choices. Chromium in tests has
// neither notifications nor a push service, so the permission and the browser's subscription are
// stand-ins (standInPush); the server never sends anything (a stand-in answers for the push
// services).

/** RFC 8291's example keys of a browser: those of the stand-in subscription. */
const PUSH_KEYS = {
  p256dh: 'BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4',
  auth: 'BTBZMqHH6r4Tts7J_aSIgg',
}
const DEVICE = 'Notifications on this device'
const SCHEDULED = 'A scheduled scan finishes'

async function expectAccessible(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
  expect(results.violations.map(violation => `${violation.id}: ${violation.nodes.map(node => node.target.join(' ')).join(', ')}`)).toEqual([])
}

/**
 * Stand-ins for the permission (not asked yet, granted when asked) and for the browser's
 * subscription (`endpoint`, an address of a known push service, with PUSH_KEYS), kept across
 * reloads. The real service worker still registers.
 */
async function standInPush(page: Page, endpoint: string): Promise<void> {
  await page.addInitScript(({ endpoint, p256dh, auth }) => {
    const PERMISSION = 'e2e.notificationPermission'
    const STORE = 'e2e.pushSubscription'
    Object.defineProperty(Notification, 'permission', {
      configurable: true,
      get: () => localStorage.getItem(PERMISSION) ?? 'default',
    })
    Notification.requestPermission = async () => {
      localStorage.setItem(PERMISSION, 'granted')
      return 'granted'
    }
    const subscriptionOf = (key: ArrayBuffer) => ({
      endpoint,
      expirationTime: null,
      options: { applicationServerKey: key, userVisibleOnly: true },
      toJSON: () => ({ endpoint, expirationTime: null, keys: { p256dh, auth } }),
      unsubscribe: async () => {
        localStorage.removeItem(STORE)
        return true
      },
    }) as unknown as PushSubscription
    const stored = () => {
      const key = localStorage.getItem(STORE)
      return key ? subscriptionOf(Uint8Array.from(atob(key), character => character.charCodeAt(0)).buffer) : null
    }
    PushManager.prototype.getSubscription = async () => stored()
    PushManager.prototype.subscribe = async (options?: PushSubscriptionOptionsInit) => {
      const key = new Uint8Array(options?.applicationServerKey as ArrayBuffer)
      localStorage.setItem(STORE, btoa(String.fromCharCode(...key)))
      return stored() as PushSubscription
    }
  }, { endpoint, ...PUSH_KEYS })
}

test('turns the notifications of this browser on, chooses what they tell, and turns them off', async ({ page }) => {
  const endpoint = 'https://fcm.googleapis.com/fcm/send/e2e-device'
  await standInPush(page, endpoint)
  const registration = () => page.waitForResponse(response =>
    response.url().endsWith('/api/push/subscription') && response.request().method() === 'PUT')

  await page.goto('/?settings=notifications')
  let dialog = page.getByRole('dialog', { name: 'Settings' })
  await expect(dialog.getByRole('tab', { name: 'Notifications' })).toHaveAttribute('aria-selected', 'true')
  const device = dialog.getByRole('switch', { name: DEVICE })
  await expect(device).not.toBeChecked()
  await expectAccessible(page)

  const registered = registration()
  // Its label, as a finger does (the switch's own checkbox is visually hidden).
  await dialog.getByText(DEVICE, { exact: true }).click()
  const answer = await registered
  expect(answer.status()).toBe(200)
  expect(answer.request().postDataJSON()).toEqual({ endpoint, keys: PUSH_KEYS, locale: 'en' })
  expect(await answer.json()).toEqual({ manual_scans: true, scheduled_scans: false, failed_scans: true, capacity: true })
  await expect(device).toBeChecked()
  const worker = await page.evaluate(async () => (await navigator.serviceWorker.ready).active?.scriptURL)
  expect(worker).toMatch(/\/sw\.js$/)
  let choices = dialog.getByRole('group', { name: 'Notify me when' })
  await expect(choices.getByRole('switch', { name: SCHEDULED })).not.toBeChecked()
  await expectAccessible(page)

  const chosen = page.waitForResponse(response =>
    response.url().endsWith('/api/push/subscription') && response.request().method() === 'PATCH')
  await choices.getByText(SCHEDULED, { exact: true }).click()
  expect((await chosen).request().postDataJSON()).toEqual({ endpoint, scheduled_scans: true })
  await expect(choices.getByRole('switch', { name: SCHEDULED })).toBeChecked()

  // Each opening registers this browser again, and its choices are kept.
  const again = registration()
  await page.reload()
  expect(await (await again).json()).toMatchObject({ scheduled_scans: true })
  dialog = page.getByRole('dialog', { name: 'Settings' })
  choices = dialog.getByRole('group', { name: 'Notify me when' })
  await expect(choices.getByRole('switch', { name: SCHEDULED })).toBeChecked()

  const removed = page.waitForResponse(response => response.url().endsWith('/api/push/unsubscribe'))
  await dialog.getByText(DEVICE, { exact: true }).click()
  expect((await removed).status()).toBe(204)
  await expect(dialog.getByRole('switch', { name: DEVICE })).not.toBeChecked()
  await expect(choices).toHaveCount(0)
  expect(await page.evaluate(() => localStorage.getItem('e2e.pushSubscription'))).toBeNull()
})
