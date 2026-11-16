import { describe, expect, test } from 'vitest'
import { LANGUAGE_STORAGE_KEY, getLanguage, resetLanguage, setLanguage } from '.'
import { en } from './locales/en'
import { es } from './locales/es'

type Tree = { readonly [key: string]: string | Tree }

function leaves(tree: Tree, prefix = ''): Map<string, string> {
  const result = new Map<string, string>()
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${key}` : key
    if (typeof value === 'string') result.set(path, value)
    else for (const [inner, text] of leaves(value, path)) result.set(inner, text)
  }
  return result
}

const placeholders = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map(match => match[1]).sort()
const tags = (text: string) => [...text.matchAll(/<(\w+)>/g)].map(match => match[1]).sort()

describe('translations', () => {
  const english = leaves(en as unknown as Tree)
  const spanish = leaves(es as unknown as Tree)

  test('Spanish has exactly the English keys', () => {
    expect([...spanish.keys()].sort()).toEqual([...english.keys()].sort())
  })

  test('every message keeps its placeholders and tags, and none is empty', () => {
    for (const [key, text] of english) {
      const translated = spanish.get(key) ?? ''
      expect(translated.trim(), key).not.toBe('')
      expect(placeholders(translated), key).toEqual(placeholders(text))
      expect(tags(translated), key).toEqual(tags(text))
    }
  })
})

describe('the language', () => {
  test('is English until another one is chosen, whatever the browser\'s, and the choice is kept in this browser', () => {
    Object.defineProperty(navigator, 'languages', { configurable: true, get: () => ['es-ES', 'es'] })
    Object.defineProperty(navigator, 'language', { configurable: true, get: () => 'es-ES' })
    expect(resetLanguage()).toBe('en')
    for (const stored of ['auto', 'fr', '']) {
      localStorage.setItem(LANGUAGE_STORAGE_KEY, stored)
      expect(resetLanguage(), stored).toBe('en')
    }

    setLanguage('es')
    expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('es')
    expect(getLanguage()).toBe('es')
    expect(resetLanguage()).toBe('es')

    // Another tab chose English.
    localStorage.setItem(LANGUAGE_STORAGE_KEY, 'en')
    window.dispatchEvent(new StorageEvent('storage', { key: LANGUAGE_STORAGE_KEY, newValue: 'en' }))
    expect(getLanguage()).toBe('en')
  })
})
