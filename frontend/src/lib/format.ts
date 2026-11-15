import { getLanguage, intlLocale, t, type Language } from '../i18n'

const UNITS = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB', 'EiB'] as const

function locale(language?: Language) {
  return intlLocale(language ?? getLanguage())
}

/** Bytes in binary units with three significant digits: `1.43 TiB`, `512 B`, `1,5 GiB` in Spanish. */
export function formatBytes(bytes: number | null | undefined, language?: Language): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return '—'
  const negative = bytes < 0
  let value = Math.abs(bytes)
  let unit = 0
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024
    unit += 1
  }
  const digits = unit === 0 ? 0 : value >= 100 ? 0 : value >= 10 ? 1 : 2
  const text = new Intl.NumberFormat(locale(language), { maximumFractionDigits: digits }).format(value)
  return `${negative ? '−' : ''}${text}\u00a0${UNITS[unit]}`
}

/** A change in bytes with its sign: `+12.3 GiB`, `−4 MiB`. */
export function formatDelta(bytes: number, language?: Language): string {
  return bytes > 0 ? `+${formatBytes(bytes, language)}` : formatBytes(bytes, language)
}

export function formatNumber(value: number | null | undefined, language?: Language): string {
  if (value === null || value === undefined) return '—'
  return new Intl.NumberFormat(locale(language)).format(value)
}

/** A share (0–1) as a percentage: `42 %` in Spanish, `42%` in English. */
export function formatPercent(share: number, language?: Language, digits = 0): string {
  return new Intl.NumberFormat(locale(language), { style: 'percent', maximumFractionDigits: digits }).format(share)
}

export function formatDateTime(iso: string | null | undefined, language?: Language, timeZone?: string): string {
  if (!iso) return '—'
  return new Intl.DateTimeFormat(locale(language), { dateStyle: 'medium', timeStyle: 'short', timeZone }).format(new Date(iso))
}

/** A short date and time: `15/11 03:12`. */
export function formatShortDateTime(iso: string, language?: Language): string {
  return new Intl.DateTimeFormat(locale(language), {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(new Date(iso))
}

export function formatDate(iso: string, language?: Language): string {
  return new Intl.DateTimeFormat(locale(language), { day: 'numeric', month: 'short' }).format(new Date(iso))
}

const STEPS: readonly [Intl.RelativeTimeFormatUnit, number][] = [
  ['second', 60], ['minute', 60], ['hour', 24], ['day', 30], ['month', 12], ['year', Infinity],
]

/** `3 hours ago`, `in 2 days`, `a few seconds ago`; `style: 'short'` gives `3 hr. ago`. */
export function formatRelative(
  iso: string | null | undefined,
  now: number = Date.now(),
  { language, style = 'long' }: { language?: Language; style?: Intl.RelativeTimeFormatStyle } = {},
): string {
  if (!iso) return '—'
  let value = (new Date(iso).getTime() - now) / 1000
  if (Math.abs(value) < 45) return value <= 0 ? t('time.justNow') : t('time.inAMoment')
  for (const [unit, size] of STEPS) {
    if (Math.abs(value) < size) {
      return new Intl.RelativeTimeFormat(locale(language), { numeric: 'auto', style }).format(Math.round(value), unit)
    }
    value /= size
  }
  return '—'
}

/** A duration such as `42 min`, `1 h 05 min`, `12 s`. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '—'
  const total = Math.max(0, Math.round(seconds))
  if (total < 60) return t('time.seconds', { value: total })
  const minutes = Math.round(total / 60)
  if (minutes < 60) return t('time.minutes', { value: minutes })
  const hours = Math.floor(minutes / 60)
  if (hours < 48) return t('time.hoursMinutes', { hours, minutes: String(minutes % 60).padStart(2, '0') })
  return t('time.days', { value: Math.round(hours / 24) })
}

/** 1st, 2nd, 3rd… in English; 1.º, 2.º… in Spanish. */
export function formatOrdinal(value: number, language: Language = getLanguage()): string {
  if (language === 'es') return `${value}.º`
  const suffix = { one: 'st', two: 'nd', few: 'rd', other: 'th', zero: 'th', many: 'th' }[
    new Intl.PluralRules('en-GB', { type: 'ordinal' }).select(value)
  ]
  return `${value}${suffix}`
}
