/** Colors of the treemap, for each theme: text on every tile keeps a contrast of at least 4.5:1. */
export type Theme = 'light' | 'dark'

/** "Size" view: a distinct hue per position, light tiles with dark text or dark tiles with light text. */
export function sizeColor(index: number, theme: Theme): string {
  const hue = (index * 47) % 360
  return theme === 'dark' ? `hsl(${hue}, 45%, 32%)` : `hsl(${hue}, 55%, 82%)`
}

/** Bytes a change needs for full intensity: a quarter of the folder, and at least 2 GB. */
function intensity(delta: number, before: number): number {
  return Math.min(1, Math.abs(delta) / Math.max(before * 0.25, 2e9))
}

/** "Changes" view: grey when unchanged, green when space was freed, red when it grew. */
export function changeColor(delta: number, before: number, theme: Theme): string {
  if (!delta) return theme === 'dark' ? '#3a3a3a' : '#e3e5e8'
  const f = intensity(delta, before)
  if (theme === 'dark') {
    return delta < 0 ? `hsl(130, ${35 + 40 * f}%, ${28 - 4 * f}%)` : `hsl(0, ${35 + 40 * f}%, ${30 - 2 * f}%)`
  }
  return delta < 0 ? `hsl(130, ${35 + 30 * f}%, ${86 - 18 * f}%)` : `hsl(0, ${45 + 30 * f}%, ${88 - 18 * f}%)`
}

/** Text color on any tile of a theme. */
export function tileText(theme: Theme): string {
  return theme === 'dark' ? '#f4f5f7' : '#14171c'
}

/** Changes smaller than this are labelled "no change" (the color still shows them). */
export const UNCHANGED_BYTES = 50 * 1000 * 1000

/** Bytes not assigned to subfolders get their own tile from this share of the parent. */
export const OTHER_SHARE = 0.005
