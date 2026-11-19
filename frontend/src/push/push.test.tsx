import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, test, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import type { PushEndpointIn, PushPreferencesIn, PushPreferencesOut, PushSubscriptionIn } from '../api/schema.gen'
import { NotificationSettings } from '../components/NotificationSettings'
import { renderWithProviders, resetApi, server } from '../test/render'
import { PUSH_STORAGE_KEY, PushProvider } from './PushContext'
import { base64UrlBytes } from './support'

// The server's VAPID key, and the one it had before a new AUTH_SECRET.
const KEY = 'BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U'
const OLD_KEY = 'BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM'
const ENDPOINT = 'https://fcm.googleapis.com/fcm/send/device-1'
const DEVICE = 'Notifications on this device'
const CHOICES = 'Notify me when'
const DEFAULTS: PushPreferencesOut = { manual_scans: true, scheduled_scans: false, failed_scans: true, capacity: true }
const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Mobile/15E148 Safari/604.1'

class FakeSubscription {
  readonly options: { applicationServerKey: ArrayBuffer | null }
  readonly unsubscribe: () => Promise<boolean>

  constructor(browser: FakeBrowser, readonly endpoint: string, key: string) {
    this.options = { applicationServerKey: base64UrlBytes(key).buffer }
    this.unsubscribe = vi.fn(async () => {
      browser.subscription = null
      return true
    })
  }

  toJSON() {
    return { endpoint: this.endpoint, expirationTime: null, keys: { p256dh: 'BBrowserKey', auth: 'AuthSecret' } }
  }
}

interface FakeBrowserOptions {
  /** `Notification.permission` before asking. */
  permission?: NotificationPermission
  /** What the person answers when asked. */
  answer?: NotificationPermission
  /** The key of a subscription the browser already has. */
  subscribedWith?: string
}

/** A secure page of a browser with service workers and the Push API (jsdom has neither). */
class FakeBrowser {
  subscription: FakeSubscription | null = null
  readonly notification: { permission: NotificationPermission; requestPermission: () => Promise<NotificationPermission> }
  readonly subscribe = vi.fn(async ({ applicationServerKey }: { applicationServerKey: Uint8Array }) => {
    const key = btoa(String.fromCharCode(...applicationServerKey)).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '')
    this.subscription = new FakeSubscription(this, `${ENDPOINT}-${key.slice(-6)}`, key)
    return this.subscription
  })
  readonly registration = { pushManager: { getSubscription: async () => this.subscription, subscribe: this.subscribe } }
  readonly register = vi.fn(async () => this.registration)

  constructor({ permission = 'default', answer = 'granted', subscribedWith }: FakeBrowserOptions = {}) {
    if (subscribedWith) this.subscription = new FakeSubscription(this, ENDPOINT, subscribedWith)
    const notification = {
      permission,
      requestPermission: vi.fn(async () => {
        notification.permission = answer
        return answer
      }),
    }
    this.notification = notification
    vi.stubGlobal('Notification', notification)
    vi.stubGlobal('PushManager', {})
    vi.stubGlobal('isSecureContext', true)
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: { register: this.register, ready: Promise.resolve(this.registration), getRegistration: async () => this.registration },
    })
  }
}

/** The server's side of /api/push: what each request sent. */
function pushApi() {
  const sent = { registered: [] as PushSubscriptionIn[], chosen: [] as PushPreferencesIn[], removed: [] as PushEndpointIn[] }
  let preferences = DEFAULTS
  server.use(
    http.get('/api/push', () => HttpResponse.json({ public_key: KEY })),
    http.put('/api/push/subscription', async ({ request }) => {
      sent.registered.push(await request.json() as PushSubscriptionIn)
      return HttpResponse.json(preferences)
    }),
    http.patch('/api/push/subscription', async ({ request }) => {
      const change = await request.json() as PushPreferencesIn
      sent.chosen.push(change)
      const { endpoint: _endpoint, ...choices } = change
      preferences = { ...preferences, ...choices } as PushPreferencesOut
      return HttpResponse.json(preferences)
    }),
    http.post('/api/push/unsubscribe', async ({ request }) => {
      sent.removed.push(await request.json() as PushEndpointIn)
      return new HttpResponse(null, { status: 204 })
    }),
  )
  return sent
}

function renderSettings() {
  return renderWithProviders(<PushProvider><NotificationSettings /></PushProvider>)
}

beforeAll(() => server.listen({ onUnhandledFrame: 'error' }))
beforeEach(() => { resetApi() })
afterEach(() => {
  server.resetHandlers()
  vi.unstubAllGlobals()
  Reflect.deleteProperty(navigator, 'serviceWorker')
  Reflect.deleteProperty(navigator, 'userAgent')
  localStorage.removeItem(PUSH_STORAGE_KEY)
})
afterAll(() => server.close())

describe('the notifications of this device', () => {
  test('turn on, choose what they tell, and turn off', async () => {
    const browser = new FakeBrowser()
    const sent = pushApi()
    const user = userEvent.setup()
    renderSettings()

    const device = screen.getByRole('switch', { name: DEVICE })
    expect(device).not.toBeChecked()
    expect(device).toHaveAccessibleDescription('The browser asks for permission the first time.')
    expect(screen.queryByRole('group', { name: CHOICES })).not.toBeInTheDocument()
    await user.click(device)
    await waitFor(() => expect(device).toBeChecked())
    expect(browser.notification.requestPermission).toHaveBeenCalledOnce()
    expect(browser.register).toHaveBeenCalledWith('/sw.js')
    expect(browser.subscribe).toHaveBeenCalledWith({ userVisibleOnly: true, applicationServerKey: base64UrlBytes(KEY) })
    const endpoint = browser.subscription?.endpoint
    expect(sent.registered).toEqual([{ endpoint, keys: { p256dh: 'BBrowserKey', auth: 'AuthSecret' }, locale: 'en' }])
    expect(localStorage.getItem(PUSH_STORAGE_KEY)).toBe('1')

    const choices = screen.getByRole('group', { name: CHOICES })
    expect(within(choices).getByRole('switch', { name: 'A scan started with "Scan now" finishes' })).toBeChecked()
    const scheduled = within(choices).getByRole('switch', { name: 'A scheduled scan finishes' })
    expect(scheduled).not.toBeChecked()
    expect(scheduled).toHaveAccessibleDescription('Off by default: they usually run at night.')
    expect(within(choices).getByRole('switch', { name: 'A scan fails or is interrupted' })).toBeChecked()
    expect(within(choices).getByRole('switch', { name: 'A volume is nearly full' })).toBeChecked()
    await user.click(scheduled)
    await waitFor(() => expect(sent.chosen).toEqual([{ endpoint, scheduled_scans: true }]))
    expect(scheduled).toBeChecked()

    await user.click(device)
    await waitFor(() => expect(device).not.toBeChecked())
    expect(sent.removed).toEqual([{ endpoint }])
    expect(browser.subscription).toBeNull()
    expect(localStorage.getItem(PUSH_STORAGE_KEY)).toBeNull()
    expect(screen.queryByRole('group', { name: CHOICES })).not.toBeInTheDocument()
  })

  test('say why they cannot be turned on here', () => {
    // Plain HTTP: browsers offer neither service workers nor notifications.
    let view = renderSettings()
    expect(screen.getByText(/^Browsers only offer notifications to sites opened over HTTPS/)).toBeInTheDocument()
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
    view.unmount()

    // Safari on an iPhone has them only for the app added to the Home Screen.
    vi.stubGlobal('isSecureContext', true)
    Object.defineProperty(navigator, 'userAgent', { configurable: true, value: IPHONE })
    view = renderSettings()
    expect(screen.getByText(/^To get notifications on an iPhone or iPad, add DiskSprawl to the Home Screen/)).toBeInTheDocument()
    view.unmount()

    new FakeBrowser({ permission: 'denied' })
    renderSettings()
    expect(screen.getByText(/^Notifications from DiskSprawl are blocked on this device/)).toBeInTheDocument()
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
  })

  test('stay off when the permission is refused', async () => {
    new FakeBrowser({ answer: 'denied' })
    const sent = pushApi()
    renderSettings()
    await userEvent.click(screen.getByRole('switch', { name: DEVICE }))
    expect(await screen.findByText(/^Notifications from DiskSprawl are blocked on this device/)).toBeInTheDocument()
    expect(sent.registered).toEqual([])
  })

  test('are registered again when DiskSprawl opens, with a new subscription after a new server key', async () => {
    localStorage.setItem(PUSH_STORAGE_KEY, '1')
    const browser = new FakeBrowser({ permission: 'granted', subscribedWith: OLD_KEY })
    const old = browser.subscription
    const sent = pushApi()
    renderSettings()

    expect(await screen.findByRole('switch', { name: 'A volume is nearly full' })).toBeChecked()
    expect(old?.unsubscribe).toHaveBeenCalledOnce()
    expect(sent.removed).toEqual([{ endpoint: ENDPOINT }])
    expect(sent.registered.map(body => body.endpoint)).toEqual([browser.subscription?.endpoint])
    expect(browser.notification.requestPermission).not.toHaveBeenCalled()
  })

  test('offer to try again when the server cannot be reached, and put back a choice it cannot save', async () => {
    localStorage.setItem(PUSH_STORAGE_KEY, '1')
    new FakeBrowser({ permission: 'granted', subscribedWith: KEY })
    pushApi()
    let registrations = 0
    server.use(
      http.put('/api/push/subscription', () => ++registrations === 1
        ? HttpResponse.json({ code: 'internal_error' }, { status: 500 })
        : HttpResponse.json(DEFAULTS)),
      http.patch('/api/push/subscription', () => HttpResponse.json({ code: 'internal_error' }, { status: 500 })),
    )
    const user = userEvent.setup()
    renderSettings()

    expect(await screen.findByText('The notifications of this device could not be loaded.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    const capacity = await screen.findByRole('switch', { name: 'A volume is nearly full' })
    await user.click(capacity)
    expect(await screen.findByText('Unexpected server error. Check the container logs.')).toBeInTheDocument()
    expect(capacity).toBeChecked()
  })
})
