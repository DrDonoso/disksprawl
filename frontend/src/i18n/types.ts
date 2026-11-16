import type { ReactNode } from 'react'
import type { en } from './locales/en'

export const LANGUAGES = ['en', 'es'] as const

/** Interface language: English (default and fallback) or Spanish. */
export type Language = (typeof LANGUAGES)[number]

/** Language setting: follow the browser, or a fixed language. */
export type LanguagePreference = Language | 'auto'

/** BCP 47 tag used by Intl and React Aria. */
export type IntlLocale = 'en-GB' | 'es-ES'

/** The English dictionary is the source of truth for every key. */
export type Dictionary = typeof en

/** A translation mirrors the English source: same keys at every level, string leaves. */
export type Messages<T> = { readonly [K in keyof T]: T[K] extends string ? string : Messages<T[K]> }

/** Plural message, chosen with Intl.PluralRules from `count`; never name a group key `other`. */
export type PluralMessage = { readonly zero?: string; readonly one: string; readonly other: string }

type Join<P extends string, K extends string> = P extends '' ? K : `${P}.${K}`

/** Dotted path of every message (`'common.close'`, `'errors.codes.invalid_cron'`, …). */
export type LeafKeys<T, P extends string = ''> = {
  [K in keyof T & string]: T[K] extends string
    ? Join<P, K>
    : T[K] extends { readonly other: string } ? Join<P, K> : LeafKeys<T[K], Join<P, K>>
}[keyof T & string]

export type TranslationKey = LeafKeys<Dictionary>

type At<T, K extends string> = K extends `${infer Head}.${infer Rest}`
  ? Head extends keyof T ? At<T[Head], Rest> : never
  : K extends keyof T ? T[K] : never

type Templates<M> = M extends string ? M : M extends Readonly<Record<string, string>> ? M[keyof M] : never
type Placeholders<S> = S extends `${string}{${infer Name}}${infer Rest}` ? Name | Placeholders<Rest> : never
type Tags<S> = S extends `${string}<${infer Tag}>${infer Rest}` ? (Tag extends `/${string}` ? never : Tag) | Tags<Rest> : never

export type ParamValue = string | number
export type Params = Readonly<Record<string, ParamValue>>

type MessageParams<M> = [M] extends [string]
  ? { readonly [N in Placeholders<M>]: ParamValue }
  : { readonly [N in 'count' | Placeholders<Templates<M>>]: N extends 'count' ? number : ParamValue }

export type ParamsOf<K extends TranslationKey> = MessageParams<At<Dictionary, K>>

/** `t(key)` without parameters, `t(key, params)` when the message has placeholders. */
export type TranslateArgs<K extends TranslationKey> = [keyof ParamsOf<K>] extends [never]
  ? [params?: undefined]
  : [TranslationKey] extends [K] ? [params?: Params] : [params: ParamsOf<K>]

export type RichTags<K extends TranslationKey> = {
  readonly [N in Tags<Templates<At<Dictionary, K>>>]: (chunks: ReactNode) => ReactNode
}

export type Translate = <K extends TranslationKey>(key: K, ...args: TranslateArgs<K>) => string
export type TranslateRich = <K extends TranslationKey>(key: K, tags: RichTags<K>, ...args: TranslateArgs<K>) => ReactNode
