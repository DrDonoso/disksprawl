/**
 * Public demo switch, set at build time by `--mode demo` (frontend/.env.demo → `VITE_DEMO=1`).
 *
 * The demo answers the whole API in the browser (MSW service worker, src/demo/handlers.ts) from
 * synthetic data kept in memory: no backend, no database, and every change lasts until reload.
 * Normal builds load nothing from src/demo: main.tsx imports it behind a literal
 * `import.meta.env.VITE_DEMO` check, which the bundler removes.
 */
export const DEMO_REPOSITORY_URL = 'https://github.com/DrDonoso/disksprawl'
