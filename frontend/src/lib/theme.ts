import { useSyncExternalStore } from 'react'
import type { Theme } from './colors'

export type { Theme } from './colors'

/** The theme setting: light, dark, or `auto` (the system's, followed live). */
export type ThemePreference = Theme | 'auto'

export const THEME_PREFERENCES: readonly ThemePreference[] = ['light', 'dark', 'auto']
export const THEME_STORAGE_KEY = 'disksprawl_theme'
const DARK = '(prefers-color-scheme: dark)'

function isTheme(value: unknown): value is Theme {
  return value === 'light' || value === 'dark'
}

/** The setting stored in this browser: `auto` when there is none. */
export function storedThemePreference(): ThemePreference {
  try {
    const value = localStorage.getItem(THEME_STORAGE_KEY)
    return isTheme(value) ? value : 'auto'
  } catch {
    return 'auto'
  }
}

function systemTheme(): Theme {
  return typeof window !== 'undefined' && window.matchMedia?.(DARK).matches ? 'dark' : 'light'
}

/**
 * Shows a setting on the page: `data-theme` on <html> picks the colors (src/styles/tokens.css) and
 * the color scheme of native controls, and the meta tags tell the browser its own colors.
 * src/lib/theme-boot.js does the same before the first paint; a unit test keeps them in step.
 */
export function applyTheme(preference: ThemePreference): Theme {
  const theme = preference === 'auto' ? systemTheme() : preference
  document.documentElement.dataset.theme = theme
  document.querySelector('meta[name="color-scheme"]')?.setAttribute('content', theme)
  for (const meta of document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"][data-theme]')) {
    meta.setAttribute('media', preference === 'auto'
      ? `(prefers-color-scheme: ${meta.dataset.theme})`
      : meta.dataset.theme === theme ? 'all' : 'not all')
  }
  return theme
}

let state: { preference: ThemePreference; theme: Theme } = (() => {
  const preference = storedThemePreference()
  return { preference, theme: applyTheme(preference) }
})()
const listeners = new Set<() => void>()

function update(preference: ThemePreference) {
  const theme = applyTheme(preference)
  if (preference === state.preference && theme === state.theme) return
  state = { preference, theme }
  for (const listener of listeners) listener()
}

if (typeof window !== 'undefined') {
  // `auto` follows the system as it changes, and every tab follows a change made in another one.
  window.matchMedia?.(DARK).addEventListener?.('change', () => {
    if (state.preference === 'auto') update('auto')
  })
  window.addEventListener('storage', event => {
    if (event.key === THEME_STORAGE_KEY || event.key === null) update(storedThemePreference())
  })
}

/** Stores the setting in this browser and applies it. */
export function setThemePreference(preference: ThemePreference) {
  try {
    if (preference === 'auto') localStorage.removeItem(THEME_STORAGE_KEY)
    else localStorage.setItem(THEME_STORAGE_KEY, preference)
  } catch {
    console.warn('The theme preference could not be stored.')
  }
  update(preference)
}

/** Reads the stored setting again (tests call it before each test). */
export function resetTheme(): ThemePreference {
  update(storedThemePreference())
  return state.preference
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

/** The theme setting of this browser; re-renders when it changes. */
export function useThemePreference(): ThemePreference {
  return useSyncExternalStore(subscribe, () => state.preference, () => 'auto')
}

/** The theme shown, light or dark (the system's under `auto`); re-renders when it changes. */
export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, () => state.theme, () => 'light')
}
