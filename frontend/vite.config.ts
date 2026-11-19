import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import type { Plugin } from 'vite'

/**
 * Security headers of the static demo: the ones src/disksprawl/security.py sends (keep them in
 * step), plus `worker-src 'self'` for the MSW service worker that answers the demo's API.
 */
const DEMO_CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self'",
  "worker-src 'self'",
  "frame-ancestors 'none'",
  "base-uri 'none'",
  "form-action 'self'",
  "object-src 'none'",
].join('; ')

const DEMO_SECURITY_HEADERS: Readonly<Record<string, string>> = {
  'Content-Security-Policy': DEMO_CSP,
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'Referrer-Policy': 'no-referrer',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
}

/**
 * Static host files of `dist-demo/`, emitted here rather than kept in public/ (which also feeds the
 * production bundle): Cloudflare's `_headers`. The SPA fallback is `not_found_handling` in
 * wrangler.jsonc.
 */
function demoSite(): Plugin {
  const headers = [
    // The service worker IS the demo's API: never serve a stale copy.
    '/mockServiceWorker.js',
    '  Cache-Control: no-cache',
    '',
    // Content-hashed by Vite.
    '/assets/*',
    '  Cache-Control: public, max-age=31536000, immutable',
    '',
    '/*',
    ...Object.entries(DEMO_SECURITY_HEADERS).map(([name, value]) => `  ${name}: ${value}`),
    '  Strict-Transport-Security: max-age=31536000',
    '',
  ].join('\n')
  return {
    name: 'disksprawl:demo-site',
    transformIndexHtml(html) {
      if (!/<title>[^<]*<\/title>/.test(html)) throw new Error('index.html has no <title> for the demo')
      return html.replace(
        /<title>[^<]*<\/title>/,
        '<title>DiskSprawl demo</title>\n    <meta name="description" content="DiskSprawl demo: a self-hosted disk usage analyzer with scheduled, gentle scans, a treemap and changes between scans, with synthetic data." />',
      )
    },
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: '_headers', source: headers })
    },
  }
}

/**
 * Files of public/ that only one of the builds may ship: public/mockServiceWorker.js is the demo's
 * API, and public/sw.js shows the notifications of a real server (the demo has none, and its scope
 * is MSW's).
 */
function withoutPublicFile(fileName: string): Plugin {
  return {
    name: `disksprawl:without-${fileName}`,
    apply: 'build',
    async writeBundle(options) {
      const { rm } = await import('node:fs/promises')
      await rm(`${options.dir}/${fileName}`, { force: true })
    },
  }
}

/**
 * src/lib/theme-boot.js, which applies the stored theme before the first paint, as a classic,
 * blocking script in <head>, after the meta tags it updates and before the stylesheets (a module
 * script runs after the first paint, and the Content Security Policy forbids inline scripts).
 * Builds emit it to /assets/ with a hash of its content, cached like the rest of the bundle; the
 * development server serves it as it is.
 */
function themeBoot(): Plugin[] {
  const file = fileURLToPath(new URL('./src/lib/theme-boot.js', import.meta.url))
  const inject = (html: string, src: string) => {
    if (!html.includes('<title>')) throw new Error('index.html has no <title> to put the theme script before')
    return html.replace('<title>', `<script src="${src}"></script>\n    <title>`)
  }
  let fileName = ''
  return [{
    name: 'disksprawl:theme-boot',
    apply: 'build',
    buildStart() {
      const source = readFileSync(file, 'utf8')
      fileName = `assets/theme-${createHash('sha256').update(source).digest('hex').slice(0, 10)}.js`
      this.emitFile({ type: 'asset', fileName, source })
    },
    transformIndexHtml: html => inject(html, `/${fileName}`),
  }, {
    name: 'disksprawl:theme-boot-dev',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use('/theme-boot.js', (_request, response) => {
        response.setHeader('Content-Type', 'text/javascript')
        response.end(readFileSync(file))
      })
    },
    transformIndexHtml: html => inject(html, '/theme-boot.js'),
  }]
}

export default defineConfig(({ mode }) => {
  // `--mode demo` (frontend/.env.demo): the static public demo, built into its own folder so it
  // never overwrites the production bundle that the backend serves.
  const demo = mode === 'demo'
  return {
    plugins: [react(), themeBoot(), demo ? [demoSite(), withoutPublicFile('sw.js')] : withoutPublicFile('mockServiceWorker.js')],
    // The demo's `/api/version` date (src/demo/handlers.ts); unused by normal builds.
    define: { __DEMO_BUILD_DATE__: JSON.stringify(new Date().toISOString().slice(0, 10)) },
    build: { outDir: demo ? 'dist-demo' : 'dist' },
    css: { modules: { localsConvention: 'camelCaseOnly' } },
    // The demo has no backend: nothing to proxy. `vite preview --mode demo` sends the demo's headers.
    server: demo ? {} : { proxy: { '/api': 'http://127.0.0.1:7780', '/health': 'http://127.0.0.1:7780' } },
    preview: demo ? { headers: { ...DEMO_SECURITY_HEADERS } } : {},
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test-setup.ts'],
      include: ['src/**/*.test.{ts,tsx}'],
      css: { modules: { classNameStrategy: 'non-scoped' } },
      // Dates are shown in the local time zone: the same one everywhere, like the frozen clock.
      env: { TZ: 'UTC' },
    },
  }
})
