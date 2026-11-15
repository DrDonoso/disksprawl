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

export async function api<T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    credentials: 'same-origin',
    signal,
    headers: method === 'GET' ? {} : { [MUTATION_HEADER]: '1', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null)
    const record = payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : {}
    const code = typeof record.code === 'string' ? record.code : ''
    const fields = record.fields && typeof record.fields === 'object' && !Array.isArray(record.fields)
      ? Object.fromEntries(Object.entries(record.fields).filter((entry): entry is [string, string] => typeof entry[1] === 'string'))
      : {}
    if (response.status === 401 && path !== '/auth/login') {
      queryClient.setQueryData<AuthStatusOut>(['auth'], previous => ({ enabled: previous?.enabled ?? true, authenticated: false }))
    }
    throw new ApiError(code, response.status, fields)
  }
  return (response.status === 204 ? undefined : await response.json()) as T
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
