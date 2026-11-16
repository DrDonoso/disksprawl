/** A rectangle laid out by {@link squarify}. */
export interface Tile<T> {
  item: T
  value: number
  x: number
  y: number
  w: number
  h: number
}

/**
 * Squarified treemap (Bruls, Huizing and van Wijk): lays out `items`, sorted from largest to
 * smallest, in the rectangle (x, y, w, h), filling rows along the shorter side and closing a row as
 * soon as adding the next item would make its worst aspect ratio worse.
 */
export function squarify<T>(items: readonly { item: T; value: number }[], x: number, y: number, w: number, h: number): Tile<T>[] {
  const out: Tile<T>[] = []
  const total = items.reduce((sum, entry) => sum + Math.max(entry.value, 0), 0)
  if (!total || w <= 0 || h <= 0) return out
  const scale = (w * h) / total
  let rest = items.filter(entry => entry.value > 0).map(entry => ({ ...entry, area: entry.value * scale }))
  while (rest.length) {
    const short = Math.min(w, h)
    let row: typeof rest = []
    let best = Infinity
    let sum = 0
    for (const candidate of rest) {
      const next = [...row, candidate]
      const nextSum = sum + candidate.area
      let largest = 0
      let smallest = Infinity
      for (const entry of next) {
        largest = Math.max(largest, entry.area)
        smallest = Math.min(smallest, entry.area)
      }
      const ratio = Math.max((short * short * largest) / (nextSum * nextSum), (nextSum * nextSum) / (short * short * smallest))
      if (ratio > best) break
      best = ratio
      row = next
      sum = nextSum
    }
    rest = rest.slice(row.length)
    const thickness = sum / short
    let offset = 0
    for (const entry of row) {
      const length = entry.area / thickness
      if (w >= h) out.push({ item: entry.item, value: entry.value, x, y: y + offset, w: thickness, h: length })
      else out.push({ item: entry.item, value: entry.value, x: x + offset, y, w: length, h: thickness })
      offset += length
    }
    if (w >= h) {
      x += thickness
      w -= thickness
    } else {
      y += thickness
      h -= thickness
    }
  }
  return out
}
