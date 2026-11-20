import { QueryClient } from '@tanstack/react-query'
import { hasMessage, t } from '../i18n'
import type { AuthStatusOut } from './schema.gen'

export const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 5_000, refetchOnWindowFocus: true } },
})

/** Header every write carries: with the same-origin check it is the backend's CSRF protection. */
export const MUTATION_HEADER = 'X-DiskSprawl-Request'

function errorText(code: string, status: number): string {
  const key = `errors.codes.${code}`
  return hasMessage(key) ? t(key) : t('errors.requestFailed', { status })
}

export class ApiError extends Error {
  constructor(public code: string, public status: number, public fields: Readonly<Record<string, string>> = {}) {
    super(errorText(code, status))
    this.name = 'ApiError'
  }
}

/** A user-facing message for anything thrown, in the active language. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return errorText(error.code, error.status)
  return t('errors.generic')
}

/** The message of a field error code (`fields` of an API error). */
export function fieldMessage(code: string | undefined): string | undefined {
  if (!code) return undefined
  const key = `errors.codes.${code}`
  return hasMessage(key) ? t(key) : t('errors.invalidField')
}

/** The ApiError of a failed response (its JSON `code` and `fields`, when it has them). */
async function failure(response: Response, path: string): Promise<ApiError> {
  const payload: unknown = await response.json().catch(() => null)
  const record = payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : {}
  const code = typeof record.code === 'string' ? record.code : ''
  const fields = record.fields && typeof record.fields === 'object' && !Array.isArray(record.fields)
    ? Object.fromEntries(Object.entries(record.fields).filter((entry): entry is [string, string] => typeof entry[1] === 'string'))
    : {}
  if (response.status === 401 && path !== '/auth/login') {
    queryClient.setQueryData<AuthStatusOut>(['auth'], previous => ({ enabled: previous?.enabled ?? true, authenticated: false }))
  }
  return new ApiError(code, response.status, fields)
}

export async function api<T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(new URL(`/api${path}`, window.location.origin), {
    method,
    credentials: 'same-origin',
    signal,
    headers: method === 'GET' ? {} : { [MUTATION_HEADER]: '1', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) throw await failure(response, path)
  return (response.status === 204 ? undefined : await response.json()) as T
}

/** The file name of a `Content-Disposition: attachment` header: `filename*` (UTF-8) first. */
export function attachmentName(header: string | null): string | null {
  if (!header) return null
  const extended = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(header)
  if (extended) {
    try {
      return decodeURIComponent(extended[1].trim())
    } catch {
      // A malformed name: the plain one follows.
    }
  }
  const plain = /filename\s*=\s*"([^"]*)"/i.exec(header) ?? /filename\s*=\s*([^;]+)/i.exec(header)
  return plain ? plain[1].trim() : null
}

/**
 * Downloads a file the API sends as an attachment (a scan's log) with the name it gives. It goes
 * through fetch, so a failure is an ApiError the page can show, and the demo can answer it.
 */
export async function download(path: string): Promise<void> {
  const response = await fetch(new URL(`/api${path}`, window.location.origin), { credentials: 'same-origin' })
  if (!response.ok) throw await failure(response, path)
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = attachmentName(response.headers.get('Content-Disposition')) ?? 'disksprawl.log'
  link.hidden = true
  document.body.append(link)
  link.click()
  link.remove()
  // Revoked once the browser has taken the file.
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

/** Query string of the defined parameters. */
export function query(params: Readonly<Record<string, string | number | null | undefined>>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}
