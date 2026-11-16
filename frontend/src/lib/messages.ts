import { hasMessage, t } from '../i18n'

/** The reason of a failed scan, translated when it starts with a known code (`engine_failed: …`). */
export function failureReason(message: string | null): string | null {
  if (!message) return null
  const [code, ...rest] = message.split(':')
  const key = `errors.codes.${code}`
  if (!hasMessage(key)) return message
  const detail = rest.join(':').trim()
  return detail ? `${t(key)} (${detail})` : t(key)
}
