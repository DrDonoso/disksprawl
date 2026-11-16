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

/** public/mockServiceWorker.js is copied into every build: only the demo may ship it. */
function withoutMockServiceWorker(): Plugin {
  return {
    name: 'disksprawl:without-mock-service-worker',
    apply: 'build',
    async writeBundle(options) {
      const { rm } = await import('node:fs/promises')
      await rm(`${options.dir}/mockServiceWorker.js`, { force: true })
    },
  }
}

export default defineConfig(({ mode }) => {
  // `--mode demo` (frontend/.env.demo): the static public demo, built into its own folder so it
  // never overwrites the production bundle that the backend serves.
  const demo = mode === 'demo'
  return {
    plugins: [react(), demo ? demoSite() : withoutMockServiceWorker()],
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
