// First, so that the modules below find window.matchMedia when they load.
import { prefersDark } from './test/media'
import '@testing-library/jest-dom/vitest'
import { afterEach, beforeEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'
import { LANGUAGE_STORAGE_KEY, resetLanguage } from './i18n'
import { THEME_STORAGE_KEY, resetTheme } from './lib/theme'
import { NOW } from './test/clock'

// Every test runs at the same moment: only `Date` is frozen (timers keep running), so what depends
// on the current time never depends on when the tests run. A test can move it with vi.setSystemTime.
vi.setSystemTime(NOW)

globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
}

// Every test starts at NOW, in English with the automatic theme and a light system theme, and with
// nothing stored.
beforeEach(() => {
  vi.setSystemTime(NOW)
  localStorage.removeItem(LANGUAGE_STORAGE_KEY)
  localStorage.removeItem(THEME_STORAGE_KEY)
  Object.defineProperty(navigator, 'languages', { configurable: true, get: () => ['en-GB', 'en'] })
  Object.defineProperty(navigator, 'language', { configurable: true, get: () => 'en-GB' })
  prefersDark(false, { notify: false })
  resetLanguage()
  resetTheme()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})
