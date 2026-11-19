/**
 * The demo API: every operation of src/api/openapi.json answered in the browser from DemoStore,
 * with the backend's request rules (the X-DiskSprawl-Request header on writes, FastAPI's parameter
 * validation, the error format). A request no handler answers gets a 501 and a console error.
 */
import { http, HttpResponse, type HttpHandler, type JsonBodyType, type PathParams } from 'msw'
import type { DirectoryIn, SettingsIn, WhatsNewSeenIn } from '../api/schema.gen'
import { DemoError, DemoStore } from './store'

/** Logged for an /api request that no handler answers (the e2e tests watch for it). */
export const UNHANDLED_PREFIX = '[demo] Unhandled API request:'

/**
 * Operations the demo cannot perform, with the reason: they answer `demo_unavailable` (501). The
 * interface never calls them in the demo.
 */
export const UNAVAILABLE_IN_DEMO: Readonly<Record<string, string>> = {
  'GET /api/push': 'Notifications need a server that sends them.',
  'PUT /api/push/subscription': 'Notifications need a server that sends them.',
  'PATCH /api/push/subscription': 'Notifications need a server that sends them.',
  'POST /api/push/unsubscribe': 'Notifications need a server that sends them.',
}

let store = new DemoStore()

/** Starts the demo over (a page reload does the same); tests pass a fixed clock. */
export function resetDemo(options?: ConstructorParameters<typeof DemoStore>[0]): DemoStore {
  store = new DemoStore(options)
  return store
}

export function demoStore(): DemoStore {
  return store
}

interface Context {
  request: Request
  params: PathParams
  url: URL
}

type Resolver = (context: Context) => unknown | Promise<unknown>

function invalid(): DemoError {
  return new DemoError('invalid_request', 422)
}

function route(resolve: Resolver, status = 200) {
  return async ({ request, params }: { request: Request; params: PathParams }) => {
    const url = new URL(request.url)
    try {
      if (!['GET', 'HEAD'].includes(request.method) && request.headers.get('X-DiskSprawl-Request') !== '1') {
        throw new DemoError('invalid_origin', 403)
      }
      const result = await resolve({ request, params, url })
      if (status === 204) return new HttpResponse(null, { status })
      return HttpResponse.json(result as JsonBodyType, { status })
    } catch (error) {
      if (error instanceof DemoError) {
        return HttpResponse.json(error.fields ? { code: error.code, fields: error.fields } : { code: error.code }, { status: error.status })
      }
      console.error(`[demo] ${request.method} ${url.pathname} failed:`, error)
      return HttpResponse.json({ code: 'internal_error' }, { status: 500 })
    }
  }
}

function id(params: PathParams, name: string): number {
  const raw = params[name]
  if (typeof raw !== 'string' || !/^\d+$/.test(raw)) throw invalid()
  return Number(raw)
}

function queryInt(url: URL, name: string, low: number, high: number): number | null {
  const raw = url.searchParams.get(name)
  if (raw === null) return null
  if (!/^\d+$/.test(raw) || Number(raw) < low || Number(raw) > high) throw invalid()
  return Number(raw)
}

async function body<T>(request: Request): Promise<T> {
  try {
    const value: unknown = await request.json()
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalid()
    return value as T
  } catch {
    throw invalid()
  }
}

async function directoryBody(request: Request): Promise<DirectoryIn> {
  const value = await body<DirectoryIn>(request)
  if (typeof value.name !== 'string' || typeof value.path !== 'string' || value.path.length > 4096) throw invalid()
  return value
}

export const unhandledHandler = http.all('/api/*', ({ request }) => {
  console.error(`${UNHANDLED_PREFIX} ${request.method} ${new URL(request.url).pathname}`)
  return HttpResponse.json({ code: 'not_implemented' }, { status: 501 })
})

const METHODS = { GET: http.get, PUT: http.put, PATCH: http.patch, POST: http.post, DELETE: http.delete } as const

function unavailable(operation: string): HttpHandler {
  const [method, path] = operation.split(' ')
  return METHODS[method as keyof typeof METHODS](path, route(() => {
    throw new DemoError('demo_unavailable', 501)
  }))
}

export const handlers: HttpHandler[] = [
  http.get('/api/version', route(() => ({ version: 'demo', build_date: __DEMO_BUILD_DATE__ }))),
  http.get('/api/auth/status', route(() => ({ enabled: false, authenticated: true }))),
  http.post('/api/auth/login', route(async ({ request }) => {
    const value = await body<{ password?: unknown }>(request)
    if (typeof value.password !== 'string' || !value.password) throw invalid()
    throw new DemoError('auth_disabled', 409)
  })),
  http.post('/api/auth/logout', route(() => null, 204)),
  http.get('/api/engines', route(() => ({ items: store.engines() }))),
  http.get('/api/browse', route(({ url }) => store.browse(url.searchParams.get('path')))),
  http.get('/api/cron/preview', route(({ url }) => {
    const expression = url.searchParams.get('expression')
    if (expression === null) throw invalid()
    return store.cronPreview(expression)
  })),
  http.get('/api/directories', route(() => ({ items: store.list() }))),
  http.post('/api/directories', route(async ({ request }) => store.create(await directoryBody(request)), 201)),
  http.put('/api/directories/order', route(async ({ request }) => {
    const value = await body<{ ids?: unknown }>(request)
    if (!Array.isArray(value.ids) || !value.ids.every(item => Number.isInteger(item))) throw invalid()
    return { items: store.order(value.ids as number[]) }
  })),
  http.get('/api/directories/:directoryId/overview', route(({ params }) => store.summary(id(params, 'directoryId')))),
  http.put('/api/directories/:directoryId', route(async ({ params, request }) => {
    const directory = id(params, 'directoryId')
    return store.update(directory, await directoryBody(request))
  })),
  http.delete('/api/directories/:directoryId', route(({ params }) => store.remove(id(params, 'directoryId')), 204)),
  http.post('/api/directories/:directoryId/scan', route(({ params }) => store.enqueue(id(params, 'directoryId')), 202)),
  http.get('/api/directories/:directoryId/tree', route(({ params, url }) =>
    store.tree(id(params, 'directoryId'), queryInt(url, 'scan', 0, 2 ** 31), url.searchParams.get('path') ?? ''))),
  http.get('/api/directories/:directoryId/diff', route(({ params, url }) =>
    store.diff(id(params, 'directoryId'), queryInt(url, 'from', 0, 2 ** 31), queryInt(url, 'to', 0, 2 ** 31), url.searchParams.get('path') ?? ''))),
  http.get('/api/directories/:directoryId/scans', route(({ params, url }) => ({
    items: store.scanList(id(params, 'directoryId'), queryInt(url, 'days', 1, 3660), queryInt(url, 'limit', 1, 2000) ?? 500),
  }))),
  http.get('/api/directories/:directoryId/capacity', route(({ params, url }) =>
    store.capacityHistory(id(params, 'directoryId'), queryInt(url, 'days', 1, 3660) ?? 30))),
  http.get('/api/scans/:scanId', route(({ params }) => store.scan(id(params, 'scanId')))),
  http.post('/api/scans/:scanId/cancel', route(({ params }) => store.cancel(id(params, 'scanId')))),
  http.get('/api/queue', route(() => ({ items: store.queue() }))),
  http.get('/api/settings', route(() => store.settingsOut())),
  http.get('/api/whats-new', route(() => store.whatsNew())),
  http.post('/api/whats-new/seen', route(async ({ request }) => store.markWhatsNewSeen(await body<WhatsNewSeenIn>(request)))),
  http.put('/api/settings', route(async ({ request }) => store.saveSettings(await body<SettingsIn>(request)))),
  ...Object.keys(UNAVAILABLE_IN_DEMO).map(unavailable),
  unhandledHandler,
]
