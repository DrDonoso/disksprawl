// The demo must answer the whole API: every /api operation of openapi.json needs a handler (or a
// reason in UNAVAILABLE_IN_DEMO), and every handler must still be an operation of the API.
import openapi from '../api/openapi.json'
import mswPackage from 'msw/package.json'
import worker from '../../public/mockServiceWorker.js?raw'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { getResponse } from 'msw'
import { UNAVAILABLE_IN_DEMO, UNHANDLED_PREFIX, handlers, resetDemo, unhandledHandler } from './handlers'

const METHODS = ['get', 'put', 'post', 'delete', 'patch']

function operations() {
  return Object.entries((openapi as { paths: Record<string, Record<string, unknown>> }).paths)
    .filter(([path]) => path.startsWith('/api/'))
    .flatMap(([path, item]) => Object.keys(item).filter(method => METHODS.includes(method))
      .map(method => ({ method: method.toUpperCase(), path: path.replace(/\{([^}]+)\}/g, ':$1') })))
}

const shape = (path: string) => path.replace(/:[^/]+/g, ':')

function routes() {
  return handlers.filter(handler => handler !== unhandledHandler).map(({ info }) => ({ method: String(info.method), path: String(info.path) }))
}

afterEach(() => vi.restoreAllMocks())

describe('the demo API', () => {
  test('answers every operation of the API, and nothing else', () => {
    const api = operations()
    expect(api.length).toBeGreaterThanOrEqual(20)
    const handled = routes()
    const key = (route: { method: string; path: string }) => `${route.method} ${shape(route.path)}`
    const missing = api.filter(operation => !handled.some(route => key(route) === key(operation)) && !Object.hasOwn(UNAVAILABLE_IN_DEMO, key(operation)))
    expect(missing.map(key), 'operations no demo handler answers').toEqual([])
    const extra = handled.filter(route => !api.some(operation => key(operation) === key(route)))
    expect(extra.map(key), 'handlers of no API operation').toEqual([])
    const keys = handled.map(key)
    expect(keys.filter((value, index) => keys.indexOf(value) !== index), 'operations answered twice').toEqual([])
  })

  test('an /api request without a handler answers 501 and logs an error', async () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})
    const response = await getResponse(handlers, new Request(new URL('/api/nothing', location.origin)))
    expect(response?.status).toBe(501)
    expect(error).toHaveBeenCalledWith(`${UNHANDLED_PREFIX} GET /api/nothing`)
  })

  test('writes need the request header, like the backend', async () => {
    const response = await getResponse(handlers, new Request(new URL('/api/directories/1/scan', location.origin), { method: 'POST' }))
    expect(response?.status).toBe(403)
    expect(await response?.json()).toEqual({ code: 'invalid_origin' })
  })

  test('what needs a real server answers that it is not available in the demo', async () => {
    for (const operation of Object.keys(UNAVAILABLE_IN_DEMO)) {
      const [method, path] = operation.split(' ')
      const request = new Request(new URL(path, location.origin), method === 'GET' ? {} : { method, headers: { 'X-DiskSprawl-Request': '1' } })
      const response = await getResponse(handlers, request)
      expect(response?.status, operation).toBe(501)
      expect(await response?.json()).toEqual({ code: 'demo_unavailable' })
    }
  })

  test("a scan's log answers as a text attachment, like the backend", async () => {
    const latest = resetDemo({ now: () => Date.now() }).summary(3).last_scan
    const response = await getResponse(handlers, new Request(new URL(`/api/scans/${latest?.id}/log`, location.origin)))
    expect(response?.status).toBe(200)
    expect(response?.headers.get('Content-Type')).toBe('text/plain; charset=utf-8')
    expect(response?.headers.get('Content-Disposition')).toMatch(/^attachment; filename="Backups_\d{4}-\d\d-\d\d_\d{4}\.log"$/)
    expect(await response?.text()).toMatch(/^DiskSprawl demo: scan \d+\nDirectory: Backups \(id 3\)\n/)
    const missing = await getResponse(handlers, new Request(new URL('/api/scans/999999/log', location.origin)))
    expect(missing?.status).toBe(404)
    expect(await missing?.json()).toEqual({ code: 'scan_not_found' })
  })

  test('the committed service worker matches the installed MSW (after upgrading msw, run `npx msw init public`)', () => {
    expect(worker).toContain(`const PACKAGE_VERSION = '${mswPackage.version}'`)
  })
})
