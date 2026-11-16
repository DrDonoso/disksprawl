import { describe, expect, test } from 'vitest'
import type { DiffOut, TreeOut } from '../api/schema.gen'
import { changeColor, sizeColor, tileText, type Theme } from './colors'
import { changeBlocks, changeLines, parentPath, sizeBlocks } from './blocks'

const GB = 1e9
const THEMES: Theme[] = ['light', 'dark']

/** WCAG contrast ratio of two CSS colors (hex or hsl). */
function contrast(a: string, b: string): number {
  const luminance = (color: string) => {
    let rgb: number[]
    if (color.startsWith('#')) rgb = [1, 3, 5].map(index => parseInt(color.slice(index, index + 2), 16) / 255)
    else {
      const [h, s, l] = color.match(/[\d.]+/g)!.map(Number)
      const sat = s / 100
      const light = l / 100
      const f = (n: number) => {
        const k = (n + h / 30) % 12
        return light - sat * Math.min(light, 1 - light) * Math.max(-1, Math.min(k - 3, 9 - k, 1))
      }
      rgb = [f(0), f(8), f(4)]
    }
    const [r, g, bl] = rgb.map(c => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl
  }
  const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (high + 0.05) / (low + 0.05)
}

describe('treemap colors', () => {
  test('changes: grey when unchanged, green when space was freed, red when it grew', () => {
    expect(changeColor(0, 100 * GB, 'dark')).toBe('#3a3a3a')
    expect(changeColor(-5 * GB, 100 * GB, 'dark')).toMatch(/^hsl\(130,/)
    expect(changeColor(5 * GB, 100 * GB, 'dark')).toMatch(/^hsl\(0,/)
    // Bigger changes are more intense: more saturated.
    const saturation = (color: string) => Number(color.match(/, ([\d.]+)%/)![1])
    expect(saturation(changeColor(50 * GB, 100 * GB, 'light'))).toBeGreaterThan(saturation(changeColor(1 * GB, 100 * GB, 'light')))
  })

  test('labels stay readable on every tile in both themes', () => {
    for (const theme of THEMES) {
      for (let index = 0; index < 60; index++) expect(contrast(sizeColor(index, theme), tileText(theme))).toBeGreaterThanOrEqual(4.5)
      for (const delta of [0, -1, 1, -1e9, 1e9, -1e13, 1e13]) {
        expect(contrast(changeColor(delta, 1e10, theme), tileText(theme))).toBeGreaterThanOrEqual(4.5)
      }
    }
  })
})

describe('treemap blocks', () => {
  const tree: TreeOut = {
    scan: {} as TreeOut['scan'], path: '', size: 1000, files: 10, dirs: 3, other_size: 4,
    children: [
      { name: 'b', path: 'b', size: 396, files: 2, dirs: 0, has_children: false },
      { name: 'a', path: 'a', size: 600, files: 8, dirs: 2, has_children: true },
    ],
  }

  test('"Size": largest first, «name / size (% of parent)», and the loose files from 0.5 %', () => {
    const blocks = sizeBlocks(tree, 'light')
    expect(blocks.map(block => block.name)).toEqual(['a', 'b'])
    expect(blocks[0].lines).toEqual(['600\u00a0B (60%)'])
    expect(blocks[0].navigable).toBe(true)
    const withOther = sizeBlocks({ ...tree, other_size: 6 }, 'light')
    expect(withOther.map(block => block.name)).toContain('Loose files and small folders')
  })

  test('"Changes": every folder of either scan, sized by max(before, after)', () => {
    const diff: DiffOut = {
      from_scan: {} as DiffOut['from_scan'], to_scan: {} as DiffOut['to_scan'], path: '', before: 300 * GB, after: 260 * GB,
      other_before: 0, other_after: 0, biggest: [],
      children: [
        { name: 'kept', path: 'kept', before: 100 * GB, after: 100.01 * GB, has_children: false },
        { name: 'gone', path: 'gone', before: 200 * GB, after: null, has_children: true },
        { name: 'new', path: 'new', before: null, after: 160 * GB, has_children: false },
      ],
    }
    const blocks = changeBlocks(diff, 'dark')
    expect(blocks.map(block => [block.name, block.value])).toEqual([['gone', 200 * GB], ['new', 160 * GB], ['kept', 100.01 * GB]])
    expect(blocks[0].lines).toEqual(['186\u00a0GiB → —', 'deleted'])
    expect(blocks[1].lines[1]).toBe('new')
    expect(blocks[2].lines[1]).toBe('no change')
  })

  test('change labels', () => {
    expect(changeLines(10 * GB, 12 * GB)).toEqual(['9.31\u00a0GiB → 11.2\u00a0GiB', '+1.86\u00a0GiB'])
    expect(changeLines(10 * GB, 10 * GB + 1e6)[1]).toBe('no change')
    expect(parentPath('a/b/c')).toBe('a/b')
    expect(parentPath('a')).toBe('')
  })
})
