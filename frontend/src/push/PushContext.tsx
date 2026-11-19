import { createContext, use, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { ApiError, api } from '../api/client'
import type { PushConfigOut, PushPreferencesIn, PushPreferencesOut, PushSubscriptionIn } from '../api/schema.gen'
import { useI18n, type Language } from '../i18n'
import { IS_DEMO, base64UrlBytes, isAppleMobile, isStandalone, pushSupported, subscribedWith } from './support'

/**
 * Notifications on this device: `demo` (the demo sends none) · `insecure` (not HTTPS: browsers
 * only offer them to secure pages) · `install` (an iPhone or iPad in Safari: only the app added to
 * the Home Screen gets them) · `unsupported` (not in this browser) · `denied` (blocked in its
 * settings) · `off` · `on`.
 */
export type PushStatus = 'demo' | 'insecure' | 'install' | 'unsupported' | 'denied' | 'off' | 'on'

/** What a device can choose to be told (src/disksprawl/notifications.py). */
export type PushChoice = keyof PushPreferencesOut

export interface PushValue {
  status: PushStatus
  /** What this device is told, once the server answered (null while they are off). */
  preferences: PushPreferencesOut | null
  /** The server could not be reached to register this device: `retry` tries again. */
  failed: boolean
  /** Turning them on or off. */
  busy: boolean
  /** Asks for permission (call it from a click: Safari only asks then) and registers this device; whether they are on. */
  enable: () => Promise<boolean>
  disable: () => Promise<void>
  /** Saves one choice of this device (shown at once, put back when it cannot be saved). */
  choose: (choice: PushChoice, on: boolean) => Promise<void>
  retry: () => void
}

export const SERVICE_WORKER = '/sw.js'
/** Set while this browser has them on (the browser keeps the subscription itself). */
export const PUSH_STORAGE_KEY = 'disksprawl_push'

const PushContext = createContext<PushValue | null>(null)

function storedOn(): boolean {
  try {
    return localStorage.getItem(PUSH_STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

function storeOn(on: boolean) {
  try {
    if (on) localStorage.setItem(PUSH_STORAGE_KEY, '1')
    else localStorage.removeItem(PUSH_STORAGE_KEY)
  } catch {
    console.warn('The notifications preference could not be stored.')
  }
}

function initialStatus(): PushStatus {
  if (IS_DEMO) return 'demo'
  if (!window.isSecureContext) return 'insecure'
  if (!pushSupported()) return isAppleMobile() && !isStandalone() ? 'install' : 'unsupported'
  if (Notification.permission === 'denied') return 'denied'
  return Notification.permission === 'granted' && storedOn() ? 'on' : 'off'
}

function subscriptionBody(subscription: PushSubscription, language: Language): PushSubscriptionIn {
  const keys = subscription.toJSON().keys ?? {}
  return { endpoint: subscription.endpoint, keys: { p256dh: keys.p256dh ?? '', auth: keys.auth ?? '' }, locale: language }
}

function register(subscription: PushSubscription, language: Language): Promise<PushPreferencesOut> {
  return api<PushPreferencesOut>('/push/subscription', 'PUT', subscriptionBody(subscription, language))
}

/** Drops a subscription made with an older key of the server, there and in the browser. */
async function forget(subscription: PushSubscription): Promise<void> {
  await api('/push/unsubscribe', 'POST', { endpoint: subscription.endpoint }).catch(() => undefined)
  await subscription.unsubscribe()
}

/**
 * The notifications of this device (src/disksprawl/notifications.py): turned on in ⚙ Settings →
 * Notifications, and registered again each time DiskSprawl opens (and when the language changes),
 * so the server keeps the device's language, its session and a subscription made with its current
 * key.
 */
export function PushProvider({ children }: { children: ReactNode }) {
  const { language } = useI18n()
  const [status, setStatus] = useState<PushStatus>(initialStatus)
  const [preferences, setPreferences] = useState<PushPreferencesOut | null>(null)
  const [failed, setFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  // Registering again after a failure, or after the server forgot this device.
  const [attempt, setAttempt] = useState(0)
  const endpoint = useRef<string | null>(null)
  const key = useRef<Promise<Uint8Array<ArrayBuffer>> | null>(null)
  // Only the answer to the latest choice is shown: an earlier one may arrive after it.
  const latest = useRef(0)

  const serverKey = useCallback(() => {
    if (!key.current) {
      const request = api<PushConfigOut>('/push').then(({ public_key }) => base64UrlBytes(public_key))
      // A failed request is tried again the next time.
      request.catch(() => {
        if (key.current === request) key.current = null
      })
      key.current = request
    }
    return key.current
  }, [])

  useEffect(() => {
    if (!pushSupported()) return
    let cancelled = false
    async function refresh() {
      if (Notification.permission !== 'granted' || !storedOn()) {
        if (!cancelled) setStatus(Notification.permission === 'denied' ? 'denied' : 'off')
        return
      }
      const registration = await navigator.serviceWorker.register(SERVICE_WORKER)
      let subscription = await registration.pushManager.getSubscription()
      if (subscription) {
        const current = await serverKey()
        if (!subscribedWith(subscription, current)) {
          // The server's key changed (a new AUTH_SECRET): the old subscription no longer works.
          await forget(subscription)
          const ready = await navigator.serviceWorker.ready
          subscription = await ready.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: current }).catch(() => null)
        }
      }
      if (!subscription) {
        storeOn(false)
        if (!cancelled) setStatus('off')
        return
      }
      const choices = await register(subscription, language)
      if (cancelled) return
      endpoint.current = subscription.endpoint
      setPreferences(choices)
      setFailed(false)
      setStatus('on')
    }
    refresh().catch((error: unknown) => {
      if (cancelled) return
      if (error instanceof ApiError && error.code === 'push_service_unsupported') {
        storeOn(false)
        setStatus('off')
        return
      }
      // Offline, or the server cannot be reached: the next opening (or `retry`) tries again.
      setFailed(true)
    })
    return () => {
      cancelled = true
    }
  }, [language, serverKey, attempt])

  const enable = useCallback(async (): Promise<boolean> => {
    if (!pushSupported()) return false
    setBusy(true)
    try {
      // First, while the click is being handled: Safari only asks for permission then.
      const permission = await Notification.requestPermission()
      if (permission !== 'granted') {
        setStatus(permission === 'denied' ? 'denied' : 'off')
        return false
      }
      await navigator.serviceWorker.register(SERVICE_WORKER)
      const [registration, current] = await Promise.all([navigator.serviceWorker.ready, serverKey()])
      let subscription = await registration.pushManager.getSubscription()
      if (subscription && !subscribedWith(subscription, current)) {
        await forget(subscription)
        subscription = null
      }
      subscription ??= await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: current })
      const choices = await register(subscription, language)
      storeOn(true)
      endpoint.current = subscription.endpoint
      setPreferences(choices)
      setFailed(false)
      setStatus('on')
      return true
    } finally {
      setBusy(false)
    }
  }, [language, serverKey])

  const disable = useCallback(async (): Promise<void> => {
    if (!pushSupported()) return
    setBusy(true)
    try {
      const registration = await navigator.serviceWorker.getRegistration()
      const subscription = await registration?.pushManager.getSubscription()
      if (subscription) {
        await api('/push/unsubscribe', 'POST', { endpoint: subscription.endpoint })
        await subscription.unsubscribe()
      }
      storeOn(false)
      endpoint.current = null
      setPreferences(null)
      setFailed(false)
      setStatus('off')
    } finally {
      setBusy(false)
    }
  }, [])

  const choose = useCallback(async (choice: PushChoice, on: boolean): Promise<void> => {
    const target = endpoint.current
    if (target === null) return
    const change = ++latest.current
    setPreferences(current => current && { ...current, [choice]: on })
    try {
      const body: PushPreferencesIn = { endpoint: target, [choice]: on }
      const saved = await api<PushPreferencesOut>('/push/subscription', 'PATCH', body)
      if (change === latest.current) setPreferences(saved)
    } catch (error) {
      setPreferences(current => current && { ...current, [choice]: !on })
      // The server forgot this device (its push service said it was gone): register it again.
      if (error instanceof ApiError && error.code === 'push_subscription_not_found') setAttempt(value => value + 1)
      throw error
    }
  }, [])

  const retry = useCallback(() => setAttempt(value => value + 1), [])

  const value = useMemo<PushValue>(
    () => ({ status, preferences, failed, busy, enable, disable, choose, retry }),
    [status, preferences, failed, busy, enable, disable, choose, retry],
  )
  return <PushContext value={value}>{children}</PushContext>
}

/** The notifications of this device; null outside {@link PushProvider}. */
export function usePush(): PushValue | null {
  return use(PushContext)
}
