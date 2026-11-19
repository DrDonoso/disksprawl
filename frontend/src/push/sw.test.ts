/**
 * public/sw.js, run with a stand-in for the worker's global scope: it shows the declarative
 * messages of the server (src/disksprawl/notifications.py) where the browser does not, and opens
 * their page.
 */
import SOURCE from '../../public/sw.js?raw'
import { describe, expect, test, vi } from 'vitest'

const ORIGIN = 'https://disks.example.com'

interface FakeWindow {
  focus: () => Promise<unknown>
  navigate: (url: string) => Promise<unknown>
}

type Listener = (event: Record<string, unknown>) => void

function loadWorker(windows: FakeWindow[] = []) {
  const listeners = new Map<string, Listener>()
  const scope = {
    location: { origin: ORIGIN },
    registration: { showNotification: vi.fn(() => Promise.resolve()) },
    clients: { claim: vi.fn(() => Promise.resolve()), matchAll: vi.fn(async () => windows), openWindow: vi.fn(async () => null) },
    skipWaiting: vi.fn(),
    addEventListener: (type: string, listener: Listener) => listeners.set(type, listener),
  }
  // The worker's script reads its global scope as `self`.
  const run = new Function('self', SOURCE) as (self: typeof scope) => void
  run(scope)

  async function dispatch(type: string, event: Record<string, unknown> = {}) {
    const pending: Promise<unknown>[] = []
    listeners.get(type)?.({ ...event, waitUntil: (promise: Promise<unknown>) => pending.push(promise) })
    await Promise.all(pending)
  }
  return { scope, dispatch }
}

const pushOf = (message: unknown) => ({ data: { json: () => message } })

describe('the service worker', () => {
  test('takes over at once', async () => {
    const { scope, dispatch } = loadWorker()
    await dispatch('install')
    await dispatch('activate')
    expect(scope.skipWaiting).toHaveBeenCalled()
    expect(scope.clients.claim).toHaveBeenCalled()
  })

  test('shows a declarative message', async () => {
    const { scope, dispatch } = loadWorker()
    const notification = {
      title: 'Scan finished: Media',
      body: '1.43 TiB, +35.2 GiB since the previous scan. Took 12 min.',
      lang: 'en-GB',
      dir: 'auto',
      navigate: `${ORIGIN}/directories/1`,
      tag: 'scan-1',
      silent: false,
      icon: `${ORIGIN}/icon-192.png`,
      badge: `${ORIGIN}/badge-96.png`,
    }
    await dispatch('push', pushOf({ web_push: 8030, notification }))
    const { title, navigate, ...options } = notification
    // It replaces the one about the same directory, and still sounds.
    expect(scope.registration.showNotification).toHaveBeenCalledWith(title, { ...options, renotify: true, data: { navigate } })
    await dispatch('push', pushOf({ web_push: 8030, notification: { title, navigate } }))
    expect(scope.registration.showNotification).toHaveBeenLastCalledWith(title, { renotify: false, data: { navigate } })
  })

  test('shows something for any other message (browsers revoke silent subscriptions)', async () => {
    const { scope, dispatch } = loadWorker()
    await dispatch('push', { data: { json: () => JSON.parse('not json') } })
    await dispatch('push', pushOf({ notification: { title: 'Without its mark', navigate: '/' } }))
    await dispatch('push', { data: null })
    expect(scope.registration.showNotification.mock.calls).toEqual([
      ['DiskSprawl', { data: { navigate: '/' } }],
      ['DiskSprawl', { data: { navigate: '/' } }],
      ['DiskSprawl', { data: { navigate: '/' } }],
    ])
  })

  test('opens the page of a tapped notification in a window of the app, or in a new one', async () => {
    const page = `${ORIGIN}/directories/3`
    const open: FakeWindow = { focus: vi.fn(async () => open), navigate: vi.fn(async () => open) }
    const tap = (navigate: string) => ({ notification: { close: vi.fn(), data: { navigate } } })

    let worker = loadWorker([open])
    const event = tap(page)
    await worker.dispatch('notificationclick', event)
    expect(event.notification.close).toHaveBeenCalled()
    expect(open.focus).toHaveBeenCalled()
    expect(open.navigate).toHaveBeenCalledWith(page)
    expect(worker.scope.clients.openWindow).not.toHaveBeenCalled()

    worker = loadWorker()
    await worker.dispatch('notificationclick', tap(page))
    expect(worker.scope.clients.openWindow).toHaveBeenCalledWith(page)

    // Never a page of another site.
    worker = loadWorker([open])
    await worker.dispatch('notificationclick', tap('https://elsewhere.example/'))
    expect(worker.scope.clients.openWindow).not.toHaveBeenCalled()
    expect(open.navigate).toHaveBeenCalledTimes(1)
  })
})
