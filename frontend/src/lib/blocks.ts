import type { DiffOut, TreeOut } from '../api/schema.gen'
import { t } from '../i18n'
import { OTHER_SHARE, UNCHANGED_BYTES, changeColor, sizeColor, type Theme } from './colors'
import { formatBytes, formatDelta, formatPercent } from './format'

/** One tile of the treemap, before layout. */
export interface Block {
  key: string
  name: string
  path: string | null
  value: number
  color: string
  lines: string[]
  title: string
  navigable: boolean
}

const OTHER = '\0other'

/** "Size" view: each folder by its size, colored by position; label «name / size (% of parent)». */
export function sizeBlocks(tree: TreeOut, theme: Theme): Block[] {
  const parent = tree.size || 1
  const blocks: Block[] = tree.children.map((child, index) => {
    const share = formatPercent(child.size / parent, undefined, 1)
    return {
      key: child.path,
      name: child.name,
      path: child.path,
      value: child.size,
      color: sizeColor(index, theme),
      lines: [`${formatBytes(child.size)} (${share})`],
      title: `${child.name} — ${formatBytes(child.size)} (${share})`,
      navigable: child.has_children,
    }
  })
  if (tree.other_size > OTHER_SHARE * parent) {
    const share = formatPercent(tree.other_size / parent, undefined, 1)
    blocks.push({
      key: OTHER,
      name: t('treemap.otherFiles'),
      path: null,
      value: tree.other_size,
      color: theme === 'dark' ? '#2b2f36' : '#d5d9de',
      lines: [`${formatBytes(tree.other_size)} (${share})`],
      title: `${t('treemap.otherFiles')} — ${formatBytes(tree.other_size)} (${share})`,
      navigable: false,
    })
  }
  return blocks.sort((a, b) => b.value - a.value)
}

/** «before → after» and «±delta» («no change» under 50 MB) of a folder in the "Changes" view. */
export function changeLines(before: number | null, after: number | null): [string, string] {
  const delta = (after ?? 0) - (before ?? 0)
  const sizes = `${before === null ? t('treemap.absent') : formatBytes(before)} → ${after === null ? t('treemap.absent') : formatBytes(after)}`
  if (before === null) return [sizes, t('treemap.new')]
  if (after === null) return [sizes, t('treemap.deleted')]
  return [sizes, Math.abs(delta) < UNCHANGED_BYTES ? t('treemap.unchanged') : formatDelta(delta)]
}

/** "Changes" view: each folder of either scan sized by max(before, after), green when it shrank,
 * red when it grew; folders that disappeared stay visible. */
export function changeBlocks(diff: DiffOut, theme: Theme): Block[] {
  const parent = Math.max(diff.before ?? 0, diff.after ?? 0) || 1
  const blocks: Block[] = diff.children.map(child => {
    const lines = changeLines(child.before, child.after)
    return {
      key: child.path,
      name: child.name,
      path: child.path,
      value: Math.max(child.before ?? 0, child.after ?? 0),
      color: changeColor((child.after ?? 0) - (child.before ?? 0), child.before ?? 0, theme),
      lines,
      title: `${child.name} — ${lines.join(' · ')}`,
      navigable: child.has_children,
    }
  })
  const other = Math.max(diff.other_before, diff.other_after)
  if (other > OTHER_SHARE * parent) {
    const lines = changeLines(diff.other_before, diff.other_after)
    blocks.push({
      key: OTHER,
      name: t('treemap.otherFiles'),
      path: null,
      value: other,
      color: changeColor(diff.other_after - diff.other_before, diff.other_before, theme),
      lines,
      title: `${t('treemap.otherFiles')} — ${lines.join(' · ')}`,
      navigable: false,
    })
  }
  return blocks.sort((a, b) => b.value - a.value)
}

/** The parent of a path below the directory (`a/b` → `a`, `a` → ''). */
export function parentPath(path: string): string {
  const index = path.lastIndexOf('/')
  return index < 0 ? '' : path.slice(0, index)
}
