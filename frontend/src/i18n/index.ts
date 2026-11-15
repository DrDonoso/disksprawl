import { Fragment, createElement, useLayoutEffect, useMemo, useSyncExternalStore, type ReactNode } from 'react'
import { I18nProvider } from 'react-aria-components'
import { en } from './locales/en'
import { es } from './locales/es'
import type {
  Dictionary, IntlLocale, Language, LanguagePreference, Messages, Params, PluralMessage, Translate,
  TranslateArgs, TranslateRich, TranslationKey,
} from './types'

export type { Dictionary, Language, LanguagePreference, TranslationKey } from './types'

export const DICTIONARIES: Readonly<Record<Language, Messages<Dictionary>>> = { en, es }
export const INTL_LOCALES: Readonly<Record<Language, IntlLocale>> = { en: 'en-GB', es: 'es-ES' }
export const LANGUAGE_STORAGE_KEY = 'disksprawl_language'

export function isLanguage(value: unknown): value is Language {
  return value === 'en' || value === 'es'
}

function isPlural(value: unknown): value is PluralMessage {
  return typeof value === 'object' && value !== null && typeof (value as { other?: unknown }).other === 'string'
}

function resolve(tree: unknown, key: string): string | PluralMessage | undefined {
  let node = tree
  for (const part of key.split('.')) {
    // Own properties only: a code such as `constructor` must never reach Object.prototype.
    if (typeof node !== 'object' || node === null || !Object.hasOwn(node, part)) return undefined
    node = (node as Record<string, unknown>)[part]
  }
  return typeof node === 'string' || isPlural(node) ? node : undefined
}

/** Whether a key built at runtime (from an API error code, for example) names a message. */
export function hasMessage(key: string): key is TranslationKey {
  return resolve(DICTIONARIES.en, key) !== undefined
}

function pick(language: Language, message: string | PluralMessage, params?: Params): string {
  if (typeof message === 'string') return message
  const count = params?.count
  if (typeof count !== 'number') return message.other
  if (count === 0 && message.zero !== undefined) return message.zero
  const form = new Intl.PluralRules(INTL_LOCALES[language]).select(count)
  return (message as Readonly<Record<string, string | undefined>>)[form] ?? message.other
}

function interpolate(language: Language, template: string, params?: Params): string {
  if (!params) return template
  return template.replace(/\{(\w+)\}/g, (placeholder, name: string) => {
    if (!Object.hasOwn(params, name)) return placeholder
    const value = params[name]
    // Only `count` is formatted for the locale: other numbers arrive already formatted.
    return name === 'count' && typeof value === 'number'
      ? new Intl.NumberFormat(INTL_LOCALES[language]).format(value)
      : String(value)
  })
}

function lookup(language: Language, key: string): string | PluralMessage | undefined {
  return resolve(DICTIONARIES[language], key) ?? resolve(DICTIONARIES.en, key)
}

function translateLoose(language: Language, key: string, params?: Params): string {
  const message = lookup(language, key)
  return message === undefined ? key : interpolate(language, pick(language, message, params), params)
}

/** Message for `key` in `language` (English fallback, then the key itself). */
export const translate = translateLoose as <K extends TranslationKey>(
  language: Language, key: K, ...args: TranslateArgs<K>
) => string

const TAG = /<(\w+)>([\s\S]*?)<\/\1>/g

/** A message with `<tag>…</tag>` markers rendered by the given functions (never as HTML). */
function translateRich(language: Language, key: string, tags: Readonly<Record<string, (chunks: ReactNode) => ReactNode>>, params?: Params): ReactNode {
  const message = lookup(language, key)
  if (message === undefined) return key
  const text = pick(language, message, params)
  const nodes: ReactNode[] = []
  let cursor = 0
  // Tags are found before interpolation, so parameter values never become markup.
  for (const match of text.matchAll(TAG)) {
    if (match.index > cursor) nodes.push(interpolate(language, text.slice(cursor, match.index), params))
    const render = Object.hasOwn(tags, match[1]) ? tags[match[1]] : undefined
    const inner = interpolate(language, match[2], params)
    nodes.push(render ? render(inner) : inner)
    cursor = match.index + match[0].length
  }
  if (cursor < text.length) nodes.push(interpolate(language, text.slice(cursor), params))
  return nodes.map((node, index) => createElement(Fragment, { key: index }, node))
}

/** The stored preference: a fixed language, or `auto` (the browser's). */
export function storedPreference(): LanguagePreference {
  try {
    const value = localStorage.getItem(LANGUAGE_STORAGE_KEY)
    return isLanguage(value) ? value : 'auto'
  } catch {
    return 'auto'
  }
}

/** First supported language among the browser's preferences. */
export function browserLanguage(): Language {
  if (typeof navigator === 'undefined') return 'en'
  const preferences = navigator.languages?.length ? navigator.languages : [navigator.language]
  for (const tag of preferences) {
    const primary = typeof tag === 'string' ? tag.split(/[-_]/)[0].toLowerCase() : ''
    if (isLanguage(primary)) return primary
  }
  return 'en'
}

function detect(): Language {
  const preference = storedPreference()
  return preference === 'auto' ? browserLanguage() : preference
}

let current: Language = detect()
const listeners = new Set<() => void>()

function apply(next: Language) {
  if (next === current) return
  current = next
  for (const listener of listeners) listener()
}

export function getLanguage(): Language {
  return current
}

/** Stores the preference on this device and applies it. */
export function setLanguagePreference(preference: LanguagePreference) {
  try {
    if (preference === 'auto') localStorage.removeItem(LANGUAGE_STORAGE_KEY)
    else localStorage.setItem(LANGUAGE_STORAGE_KEY, preference)
  } catch {
    console.warn('The language preference could not be stored.')
  }
  apply(preference === 'auto' ? browserLanguage() : preference)
}

/** Detects the language again (tests call it before each test). */
export function resetLanguage(): Language {
  apply(detect())
  return current
}

export function subscribeLanguage(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

export function intlLocale(language: Language = current): IntlLocale {
  return INTL_LOCALES[language]
}

/** Translates in the active language outside React render (handlers, errors, helpers). */
export const t: Translate = (key, ...args) => translateLoose(current, key, args[0] as Params | undefined)

export interface I18n {
  language: Language
  locale: IntlLocale
  t: Translate
  rich: TranslateRich
}

/** The active language and its translators; re-renders when the language changes. */
export function useI18n(): I18n {
  const language = useSyncExternalStore(subscribeLanguage, getLanguage, getLanguage)
  return useMemo(() => ({
    language,
    locale: INTL_LOCALES[language],
    t: ((key: TranslationKey, params?: Params) => translateLoose(language, key, params)) as Translate,
    rich: ((key: TranslationKey, tags: Readonly<Record<string, (chunks: ReactNode) => ReactNode>>, params?: Params) =>
      translateRich(language, key, tags, params)) as TranslateRich,
  }), [language])
}

/** Keeps `<html lang>` in step and makes React Aria format in the active language. */
export function I18nRoot({ children }: { children: ReactNode }) {
  const { language, locale } = useI18n()
  useLayoutEffect(() => {
    document.documentElement.lang = language
  }, [language])
  return createElement(I18nProvider, { locale, children })
}
