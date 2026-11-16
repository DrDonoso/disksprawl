/** Schedule presets offered by the form; anything else is a custom cron expression. */
export const PRESETS = {
  manual: null,
  hourly: '0 * * * *',
  every6h: '0 */6 * * *',
  daily: '0 3 * * *',
  weekly: '0 3 * * 1',
} as const

export type Preset = keyof typeof PRESETS | 'custom'

export function presetOf(cron: string | null): Preset {
  for (const [name, expression] of Object.entries(PRESETS)) {
    if (expression === cron) return name as Preset
  }
  return 'custom'
}

/** Five fields of numbers, `*`, ranges, lists and steps (the server validates the details). */
export function looksLikeCron(expression: string): boolean {
  const fields = expression.trim().split(/\s+/)
  return fields.length === 5 && fields.every(field => /^[\d*,/\-A-Za-z]+$/.test(field))
}
