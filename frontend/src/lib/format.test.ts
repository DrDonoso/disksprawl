import { describe, expect, test } from 'vitest'
import { setLanguagePreference } from '../i18n'
import { formatBytes, formatDelta, formatDuration, formatOrdinal, formatPercent, formatRelative } from './format'
import { NOW } from '../test/clock'

describe('formatting', () => {
  test('bytes use binary units with three significant digits, in the active language', () => {
    expect(formatBytes(512)).toBe('512\u00a0B')
    expect(formatBytes(1536)).toBe('1.5\u00a0KiB')
    expect(formatBytes(1.43 * 2 ** 40)).toBe('1.43\u00a0TiB')
    expect(formatBytes(123.4 * 2 ** 30)).toBe('123\u00a0GiB')
    expect(formatBytes(null)).toBe('—')
    expect(formatDelta(5 * 2 ** 30)).toBe('+5\u00a0GiB')
    expect(formatDelta(-5 * 2 ** 20)).toBe('−5\u00a0MiB')
    setLanguagePreference('es')
    expect(formatBytes(1.5 * 2 ** 30)).toBe('1,5\u00a0GiB')
    expect(formatPercent(0.42)).toBe('42\u00a0%')
  })

  test('relative times, durations and ordinals', () => {
    expect(formatRelative(new Date(NOW - 3 * 3600_000).toISOString(), NOW)).toBe('3 hours ago')
    expect(formatRelative(new Date(NOW - 10_000).toISOString(), NOW)).toBe('a few seconds ago')
    expect(formatRelative(new Date(NOW + 2 * 86_400_000).toISOString(), NOW)).toBe('in 2 days')
    expect(formatDuration(42)).toBe('42 s')
    expect(formatDuration(42 * 60)).toBe('42 min')
    expect(formatDuration(65 * 60)).toBe('1 h 05 min')
    expect(formatOrdinal(1)).toBe('1st')
    expect(formatOrdinal(2)).toBe('2nd')
    expect(formatOrdinal(13)).toBe('13th')
    setLanguagePreference('es')
    expect(formatRelative(new Date(NOW - 3 * 3600_000).toISOString(), NOW)).toBe('hace 3 horas')
    expect(formatRelative(new Date(NOW - 3 * 3600_000).toISOString(), NOW, { style: 'short' })).toBe('hace 3 h')
    expect(formatOrdinal(2)).toBe('2.º')
  })
})
