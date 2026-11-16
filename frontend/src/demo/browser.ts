/**
 * Starts the demo's API: the Mock Service Worker (public/mockServiceWorker.js) with every handler.
 * main.tsx awaits it before the first render, so no request escapes to the static host.
 */
import { setupWorker } from 'msw/browser'
import { getLanguage, t } from '../i18n'
import { handlers } from './handlers'

/** Plain DOM (React never mounted): why the demo cannot run here. */
function showStartError(root: HTMLElement): void {
  document.documentElement.lang = getLanguage()
  const box = document.createElement('section')
  box.className = 'card'
  box.style.cssText = 'max-width:560px;margin:10vh auto;padding:24px;display:grid;gap:10px'
  box.setAttribute('role', 'alert')
  const title = document.createElement('h1')
  title.textContent = t('demo.startError.title')
  const text = document.createElement('p')
  text.textContent = t('demo.startError.description')
  box.append(title, text)
  root.replaceChildren(box)
}

/** Registers the worker; false (with an explanation on screen) where service workers are unavailable. */
export async function startDemo(root: HTMLElement): Promise<boolean> {
  try {
    if (!('serviceWorker' in navigator)) throw new Error('Service workers are not available here')
    await setupWorker(...handlers).start({
      serviceWorker: { url: '/mockServiceWorker.js' },
      quiet: true,
      // Scripts, styles and icons come from the static host; /api never gets here (catch-all).
      onUnhandledFrame: 'bypass',
    })
    return true
  } catch (error) {
    console.error('The demo API could not start:', error)
    showStartError(root)
    return false
  }
}
