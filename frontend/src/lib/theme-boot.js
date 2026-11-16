/*
 * Applies the theme stored in this browser before the first paint, so that the page never flashes
 * in the other theme. vite.config.ts loads it as a classic, blocking script in <head>: a module
 * script would run after the first paint, and the Content Security Policy forbids inline scripts.
 * It does what applyTheme() in src/lib/theme.ts does, which a unit test checks.
 */
;(() => {
  let preference = null
  try {
    preference = localStorage.getItem('disksprawl_theme')
  } catch {
    // Storage may be blocked: the system's theme, then.
  }
  if (preference !== 'light' && preference !== 'dark') preference = 'auto'
  const dark = Boolean(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)
  const theme = preference === 'auto' ? (dark ? 'dark' : 'light') : preference
  document.documentElement.dataset.theme = theme
  const scheme = document.querySelector('meta[name="color-scheme"]')
  if (scheme) scheme.setAttribute('content', theme)
  for (const meta of document.querySelectorAll('meta[name="theme-color"][data-theme]')) {
    meta.setAttribute('media', preference === 'auto'
      ? `(prefers-color-scheme: ${meta.dataset.theme})`
      : meta.dataset.theme === theme ? 'all' : 'not all')
  }
})()
