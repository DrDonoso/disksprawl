/**
 * window.matchMedia for the unit tests (jsdom has none): no query matches, except that a test can
 * make the system prefer the dark theme with `prefersDark(true)`, which notifies its listeners.
 * src/test-setup.ts imports it before anything else, so modules see it when they load.
 */
const DARK = '(prefers-color-scheme: dark)'
let dark = false
const darkQueries = new Set<MediaQuery>()

class MediaQuery extends EventTarget {
  onchange: ((event: Event) => void) | null = null

  constructor(readonly media: string) {
    super()
  }

  get matches(): boolean {
    return this.media === DARK && dark
  }

  addListener(listener: (event: Event) => void) {
    this.addEventListener('change', listener)
  }

  removeListener(listener: (event: Event) => void) {
    this.removeEventListener('change', listener)
  }
}

window.matchMedia = (media: string) => {
  const query = new MediaQuery(media)
  if (media === DARK) darkQueries.add(query)
  return query as unknown as MediaQueryList
}

/** Makes the system prefer the dark theme, or not, as a change of the system's setting would. */
export function prefersDark(value: boolean, { notify = true }: { notify?: boolean } = {}) {
  if (value === dark) return
  dark = value
  if (notify) for (const query of darkQueries) query.dispatchEvent(new Event('change'))
}
