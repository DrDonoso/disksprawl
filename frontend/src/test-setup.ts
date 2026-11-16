import '@testing-library/jest-dom/vitest'
import { afterEach, beforeEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'
import { LANGUAGE_STORAGE_KEY, resetLanguage } from './i18n'
import { NOW } from './test/clock'

// Every test runs at the same moment: only `Date` is frozen (timers keep running), so what depends
// on the current time never depends on when the tests run. A test can move it with vi.setSystemTime.
vi.setSystemTime(NOW)

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

// Every test starts in English, with no stored preference, at NOW.
beforeEach(() => {
  vi.setSystemTime(NOW)
  localStorage.removeItem(LANGUAGE_STORAGE_KEY)
  Object.defineProperty(navigator, 'languages', { configurable: true, get: () => ['en-GB', 'en'] })
  Object.defineProperty(navigator, 'language', { configurable: true, get: () => 'en-GB' })
  resetLanguage()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})
