/**
 * What this browser can do with push notifications (src/disksprawl/notifications.py): they need a
 * secure page (HTTPS, or localhost), and Safari only offers them to the apps added to the Home
 * Screen of an iPhone or iPad (iOS 16.4 and later). The demo never offers them: its service worker
 * is its API.
 */

/** Built as the public demo (frontend/.env.demo). */
export const IS_DEMO = import.meta.env.VITE_DEMO === '1'

/** Service workers, the Push API and notifications, on a secure page; never in the demo. */
export function pushSupported(): boolean {
  return !IS_DEMO && window.isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
}

/** Opened as an installed app (Home Screen, "Install app"), not in a browser tab. */
export function isStandalone(): boolean {
  const iosStandalone = (navigator as Navigator & { standalone?: boolean }).standalone === true
  return iosStandalone || (window.matchMedia?.('(display-mode: standalone)').matches ?? false)
}

/** An iPhone or iPad (iPadOS calls itself a Mac, one with a touch screen). */
export function isAppleMobile(): boolean {
  return /\b(iPhone|iPad|iPod)\b/.test(navigator.userAgent) || (/\bMacintosh\b/.test(navigator.userAgent) && navigator.maxTouchPoints > 1)
}

/** URL-safe base64 (with or without padding) → bytes: the server's key as `applicationServerKey`. */
export function base64UrlBytes(text: string): Uint8Array<ArrayBuffer> {
  const base64 = text.replaceAll('-', '+').replaceAll('_', '/').replace(/=+$/, '')
  const binary = atob(base64 + '='.repeat((4 - (base64.length % 4)) % 4))
  return Uint8Array.from(binary, character => character.charCodeAt(0))
}

/** Whether `subscription` was made with `key` (browsers that cannot tell are taken at their word). */
export function subscribedWith(subscription: PushSubscription, key: Uint8Array): boolean {
  const current = subscription.options?.applicationServerKey
  if (!current) return true
  const bytes = new Uint8Array(current)
  return bytes.length === key.length && bytes.every((value, index) => value === key[index])
}
