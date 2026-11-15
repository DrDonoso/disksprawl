import { useMemo, type ReactNode } from 'react'
import { useTheme, useElementSize } from '../lib/hooks'
import { squarify } from '../lib/squarify'
import { tileText } from '../lib/colors'
import styles from './Treemap.module.css'

export interface TreemapItem {
  key: string
  name: string
  value: number
  color: string
  /** Lines under the name, shown when the tile is tall enough. */
  lines: string[]
  /** Full description: the tooltip and, for tiles that open a folder, the accessible name. */
  title: string
  onOpen?: () => void
}

/** Tiles smaller than this (px) cannot be read or aimed at: they are drawn but not focusable. */
const MIN_INTERACTIVE = 6

/** A squarified treemap of `items` (largest first), filling its container. */
export function Treemap({ items, label, empty }: { items: TreemapItem[]; label: string; empty?: ReactNode }) {
  const [ref, size] = useElementSize<HTMLDivElement>()
  const theme = useTheme()
  const tiles = useMemo(
    () => squarify(items.map(item => ({ item, value: item.value })), 0, 0, size.width, size.height),
    [items, size.width, size.height],
  )
  const color = tileText(theme)
  return <div ref={ref} className={styles.map} role="group" aria-label={label}>
    {tiles.map(tile => {
      const { item } = tile
      const style = { left: tile.x, top: tile.y, width: tile.w, height: tile.h, background: item.color, color }
      const content = tile.w > 54 && tile.h > 30 ? <>
        <span className={styles.name}>{item.name}</span>
        {tile.h > 50 && item.lines.map((line, index) => <span key={index} className={styles.line}>{line}</span>)}
      </> : null
      return item.onOpen && tile.w >= MIN_INTERACTIVE && tile.h >= MIN_INTERACTIVE
        ? <button key={item.key} type="button" className={`${styles.tile} ${styles.open}`} style={style}
          title={item.title} aria-label={item.title} onClick={item.onOpen}>{content}</button>
        : <div key={item.key} className={styles.tile} style={style} title={item.title} aria-hidden="true">{content}</div>
    })}
    {tiles.length === 0 && size.width > 0 && <div className={styles.empty}>{empty}</div>}
  </div>
}
