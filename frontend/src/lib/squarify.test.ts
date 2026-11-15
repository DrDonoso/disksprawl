import { describe, expect, test } from 'vitest'
import { squarify } from './squarify'

const items = [6, 6, 4, 3, 2, 2, 1].map((value, index) => ({ item: `item-${index}`, value }))

describe('squarify', () => {
  test('every tile gets an area proportional to its value and the tiles fill the rectangle', () => {
    const tiles = squarify(items, 0, 0, 600, 400)
    const total = items.reduce((sum, entry) => sum + entry.value, 0)
    expect(tiles).toHaveLength(items.length)
    for (const tile of tiles) {
      expect(tile.w * tile.h).toBeCloseTo((tile.value / total) * 600 * 400, 6)
      expect(tile.x).toBeGreaterThanOrEqual(-1e-9)
      expect(tile.y).toBeGreaterThanOrEqual(-1e-9)
      expect(tile.x + tile.w).toBeLessThanOrEqual(600 + 1e-9)
      expect(tile.y + tile.h).toBeLessThanOrEqual(400 + 1e-9)
    }
    const area = tiles.reduce((sum, tile) => sum + tile.w * tile.h, 0)
    expect(area).toBeCloseTo(600 * 400, 6)
  })

  test('tiles never overlap', () => {
    const tiles = squarify(items, 10, 20, 500, 300)
    for (const [index, a] of tiles.entries()) {
      for (const b of tiles.slice(index + 1)) {
        const overlapX = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)
        const overlapY = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y)
        expect(overlapX <= 1e-9 || overlapY <= 1e-9).toBe(true)
      }
    }
  })

  test('tiles stay close to squares', () => {
    for (const tile of squarify(items, 0, 0, 600, 400)) {
      expect(Math.max(tile.w / tile.h, tile.h / tile.w)).toBeLessThan(4)
    }
  })

  test('empty values and empty rectangles give no tiles', () => {
    expect(squarify([{ item: 'a', value: 0 }, { item: 'b', value: 5 }], 0, 0, 100, 100).map(tile => tile.item)).toEqual(['b'])
    expect(squarify(items, 0, 0, 0, 100)).toEqual([])
    expect(squarify([], 0, 0, 100, 100)).toEqual([])
  })
})
