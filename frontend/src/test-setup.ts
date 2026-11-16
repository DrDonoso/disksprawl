import '@testing-library/jest-dom/vitest'
import { afterEach, beforeEach } from 'vitest'
import { cleanup } from '@testing-library/react'
import { LANGUAGE_STORAGE_KEY, resetLanguage } from './i18n'

globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
}

if (!window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false, media: query, onchange: null,
    addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false,
  }) as MediaQueryList
}

// Every test starts in English, with no stored preference.
beforeEach(() => {
  localStorage.removeItem(LANGUAGE_STORAGE_KEY)
  Object.defineProperty(navigator, 'languages', { configurable: true, get: () => ['en-GB', 'en'] })
  Object.defineProperty(navigator, 'language', { configurable: true, get: () => 'en-GB' })
  resetLanguage()
})

afterEach(() => cleanup())
