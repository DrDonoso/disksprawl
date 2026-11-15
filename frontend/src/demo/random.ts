/** A small deterministic pseudo-random generator (mulberry32): the same seed, the same demo. */
export function random(seed: number) {
  let state = seed >>> 0
  const next = () => {
    state = (state + 0x6d2b79f5) >>> 0
    let value = state
    value = Math.imul(value ^ (value >>> 15), value | 1)
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61)
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296
  }
  return {
    next,
    /** A number in [low, high). */
    between: (low: number, high: number) => low + (high - low) * next(),
    /** An integer in [low, high]. */
    int: (low: number, high: number) => Math.floor(low + (high - low + 1) * next()),
    pick: <T,>(items: readonly T[]): T => items[Math.floor(next() * items.length)],
    chance: (probability: number) => next() < probability,
  }
}

/** A stable seed for a text (FNV-1a). */
export function seedOf(text: string): number {
  let hash = 0x811c9dc5
  for (let index = 0; index < text.length; index++) {
    hash ^= text.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193)
  }
  return hash >>> 0
}
