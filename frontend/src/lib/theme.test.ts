import { afterEach, beforeEach, describe, expect, test } from 'vitest'
import html from '../../index.html?raw'
import { prefersDark } from '../test/media'
import bootScript from './theme-boot.js?raw'
import { THEME_STORAGE_KEY, applyTheme, resetTheme, setThemePreference, storedThemePreference } from './theme'

/** The meta tags of index.html, which the theme updates, in the test's document. */
function loadHead() {
  document.head.innerHTML = new DOMParser().parseFromString(html, 'text/html').head.innerHTML
  delete document.documentElement.dataset.theme
}

/** What the theme changes on the page. */
function page() {
  return {
    theme: document.documentElement.dataset.theme,
    colorScheme: document.querySelector('meta[name="color-scheme"]')?.getAttribute('content'),
    themeColors: [...document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')].map(meta => `${meta.content} ${meta.getAttribute('media')}`),
  }
}

beforeEach(loadHead)
afterEach(() => { document.head.innerHTML = '' })

describe('the theme', () => {
  test('follows the system until one is chosen, and the choice is kept in this browser', () => {
    expect(resetTheme()).toBe('auto')
    expect(page()).toEqual({
      theme: 'light', colorScheme: 'light',
      themeColors: ['#f5f6f8 (prefers-color-scheme: light)', '#14171c (prefers-color-scheme: dark)'],
    })
    prefersDark(true)
    expect(page().theme).toBe('dark')

    setThemePreference('light')
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('light')
    expect(page()).toEqual({ theme: 'light', colorScheme: 'light', themeColors: ['#f5f6f8 all', '#14171c not all'] })
    prefersDark(false)
    prefersDark(true)
    expect(page().theme).toBe('light')

    setThemePreference('auto')
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBeNull()
    expect(page().theme).toBe('dark')

    // Another tab chose the light theme.
    localStorage.setItem(THEME_STORAGE_KEY, 'light')
    window.dispatchEvent(new StorageEvent('storage', { key: THEME_STORAGE_KEY, newValue: 'light' }))
    expect(storedThemePreference()).toBe('light')
    expect(page().theme).toBe('light')
  })

  test('theme-boot.js, which runs before the first paint, does what applyTheme does', () => {
    for (const stored of [null, 'light', 'dark', 'auto', 'purple']) {
      for (const dark of [false, true]) {
        prefersDark(dark, { notify: false })
        if (stored === null) localStorage.removeItem(THEME_STORAGE_KEY)
        else localStorage.setItem(THEME_STORAGE_KEY, stored)
        loadHead()
        new Function(bootScript)()
        const booted = page()
        loadHead()
        applyTheme(storedThemePreference())
        expect(booted, `${stored} with a ${dark ? 'dark' : 'light'} system`).toEqual(page())
        expect(booted.theme).toBe(stored === 'light' || stored === 'dark' ? stored : dark ? 'dark' : 'light')
      }
    }
  })
})
