import { useMemo, type ReactNode, type RefObject } from 'react'
import { useSearchParams } from 'react-router'
import { ToggleButton, ToggleButtonGroup } from 'react-aria-components'
import { ApiError, errorMessage } from '../api/client'
import { useDiff, useScans, useTree } from '../api/hooks'
import type { DiffChildOut, DirectorySummaryOut, ScanOut, TreeChildOut } from '../api/schema.gen'
import { useI18n } from '../i18n'
import { changeBlocks, changeLines, parentPath, sizeBlocks, type Block } from '../lib/blocks'
import { formatBytes, formatDateTime, formatDelta, formatPercent } from '../lib/format'
import { useTheme } from '../lib/theme'
import { Icon } from './Icon'
import { Treemap, type TreemapItem } from './Treemap'
import { Notice } from './ui'
import styles from './Explorer.module.css'

type View = 'size' | 'changes'

/** A scan chosen in the address (`scan`: the one shown, `from`: the one compared with), or null. */
export function scanParam(params: URLSearchParams, name: 'scan' | 'from'): number | null {
  const value = Number(params.get(name))
  return Number.isInteger(value) && value > 0 ? value : null
}

function scanText(scan: ScanOut): string {
  return `${formatDateTime(scan.finished_at)} · ${formatBytes(scan.total_bytes)}`
}

function Delta({ before, after }: { before: number | null; after: number | null }) {
  const { t } = useI18n()
  if (before === null) return <span className="delta-up">{t('treemap.new')}</span>
  if (after === null) return <span className="delta-down">{t('treemap.deleted')}</span>
  const delta = after - before
  if (!delta) return <span className="muted">0</span>
  return <span className={delta > 0 ? 'delta-up' : 'delta-down'}>{formatDelta(delta)}</span>
}

/** A notice about the scan on screen, with the way back to the latest one. */
function LatestNotice({ children, onLatest }: { children: ReactNode; onLatest: () => void }) {
  const { t } = useI18n()
  return <div className={`notice ${styles.scanNotice}`} role="status">
    {children}
    <button type="button" className={`button secondary small ${styles.latest}`} onClick={onLatest}>{t('treemap.showLatest')}</button>
  </div>
}

/**
 * The folders of a directory in one of its scans (the latest, or the one in the address's `scan`):
 * treemap, breadcrumbs, the table of the level and the biggest changes since an earlier scan.
 */
export function Explorer({ summary, ref }: { summary: DirectorySummaryOut; ref?: RefObject<HTMLElement | null> }) {
  const { t } = useI18n()
  const theme = useTheme()
  const id = summary.directory.id
  const [params, setParams] = useSearchParams()
  const path = params.get('path') ?? ''
  const chosen = scanParam(params, 'scan')
  const scans = useScans(id, 3650)
  const detailed = useMemo(
    () => (scans.data?.items ?? []).filter(scan => scan.status === 'completed' && scan.has_detail),
    [scans.data],
  )
  const latest = detailed[0]
  const shown = chosen === null ? latest : detailed.find(scan => scan.id === chosen)
  // The scans it can be compared with: the earlier ones that keep their folders.
  const earlier = useMemo(() => shown
    ? detailed.filter(scan => scan.id !== shown.id && Date.parse(scan.finished_at ?? '') <= Date.parse(shown.finished_at ?? ''))
    : [], [detailed, shown])
  const canCompare = earlier.length > 0
  const fromParam = scanParam(params, 'from')
  const from = earlier.some(scan => scan.id === fromParam) ? fromParam : null
  const view: View = params.get('view') === 'changes' && canCompare ? 'changes' : 'size'
  const tree = useTree(id, path, chosen, view === 'size')
  const diff = useDiff(id, path, from, chosen, canCompare)

  function go(changes: { path?: string; view?: View; from?: number | null; scan?: number | null }) {
    setParams(previous => {
      const next = new URLSearchParams(previous)
      for (const [key, value] of Object.entries(changes)) {
        if (value === null || value === '' || (key === 'view' && value === 'size')) next.delete(key)
        else next.set(key, String(value))
      }
      return next
    })
  }

  const blocks: Block[] = useMemo(() => {
    if (view === 'changes') return diff.data ? changeBlocks(diff.data, theme) : []
    return tree.data ? sizeBlocks(tree.data, theme) : []
  }, [view, tree.data, diff.data, theme])

  const items: TreemapItem[] = blocks.map(block => ({
    key: block.key,
    name: block.name,
    value: block.value,
    color: block.color,
    lines: block.lines,
    title: block.navigable ? t('treemap.openTitle', { title: block.title }) : block.title,
    onOpen: block.navigable && block.path !== null ? () => go({ path: block.path ?? '' }) : undefined,
  }))

  const segments = path ? path.split('/') : []
  const current = view === 'changes' ? diff : tree
  const code = current.error instanceof ApiError ? current.error.code : null
  // The chosen scan no longer exists or no longer keeps its folders (an old link, or pruned since):
  // whatever data is left on screen is the last it had.
  const gone = code === 'scan_not_found' || code === 'scan_without_detail'
  const missing = current.error instanceof ApiError && current.error.status === 404 && !gone
  // The changes of the rows in the "Size" view, only when they are of the same scan and folder: a
  // query switched off or still loading keeps the data of the scan or folder shown before.
  const diffByName = new Map((canCompare && diff.data && tree.data && diff.data.path === tree.data.path
    && diff.data.to_scan.id === tree.data.scan.id ? diff.data.children : []).map(child => [child.name, child]))
  const total = view === 'changes'
    ? Math.max(diff.data?.before ?? 0, diff.data?.after ?? 0)
    : tree.data?.size ?? 0
  const rows: (TreeChildOut | DiffChildOut)[] = gone ? [] : view === 'changes' ? diff.data?.children ?? [] : tree.data?.children ?? []
  const past = chosen !== null && chosen !== summary.last_scan?.id
  const onScreen = view === 'changes' ? diff.data?.to_scan : tree.data?.scan
  const pastScan = past && !gone ? shown ?? (onScreen?.id === chosen ? onScreen : undefined) : undefined
  const showLatest = () => {
    go({ scan: null, from: null })
    // The notice with this button goes away: the keyboard stays in the treemap's section.
    ref?.current?.focus({ preventScroll: true })
  }
  // The table and the biggest changes: under the treemap, or beside it when the card is wide.
  const side = !gone && (rows.length > 0 || Boolean(canCompare && diff.data))

  return <section ref={ref} tabIndex={-1} className={`card ${styles.explorer}`} aria-labelledby="explorer-title">
    <div className={styles.toolbar}>
      <h2 id="explorer-title">{t('treemap.title')}</h2>
      <ToggleButtonGroup aria-label={t('treemap.viewLabel')} selectionMode="single" disallowEmptySelection
        selectedKeys={[view]} className={styles.toggle}
        onSelectionChange={keys => go({ view: ([...keys][0] as View) ?? 'size' })}>
        <ToggleButton id="size" className={styles.toggleButton}>{t('treemap.size')}</ToggleButton>
        <ToggleButton id="changes" className={styles.toggleButton} isDisabled={!canCompare}>{t('treemap.changes')}</ToggleButton>
      </ToggleButtonGroup>
      {detailed.length > 1 && <label className={styles.compare}>
        <span>{t('treemap.scan')}</span>
        <select className="input" value={shown && shown !== latest ? shown.id : ''}
          onChange={event => go({ scan: Number(event.target.value) || null, from: null })}>
          {detailed.map((scan, index) => <option key={scan.id} value={index ? scan.id : ''}>
            {index ? scanText(scan) : t('treemap.latestScan', { scan: scanText(scan) })}
          </option>)}
        </select>
      </label>}
      {view === 'changes' && <label className={styles.compare}>
        <span>{t('treemap.compareWith')}</span>
        <select className="input" value={from ?? ''} onChange={event => go({ from: Number(event.target.value) || null })}>
          <option value="">{t('treemap.previousScan')}</option>
          {earlier.map(scan => <option key={scan.id} value={scan.id}>{scanText(scan)}</option>)}
        </select>
      </label>}
    </div>
    {pastScan && <LatestNotice onLatest={showLatest}>
      <Icon name="history" size={18} />
      <span>{t('treemap.pastScan', { date: formatDateTime(pastScan.finished_at) })}</span>
    </LatestNotice>}
    <nav aria-label={t('treemap.breadcrumbs')} className={styles.breadcrumbs}>
      <ol>
        <li>{segments.length
          ? <button type="button" className={styles.crumb} onClick={() => go({ path: '' })}>{summary.directory.name}</button>
          : <span aria-current="page">{summary.directory.name}</span>}</li>
        {segments.map((segment, index) => {
          const target = segments.slice(0, index + 1).join('/')
          return <li key={target}>
            <Icon name="chevron" size={14} />
            {index === segments.length - 1
              ? <span aria-current="page">{segment}</span>
              : <button type="button" className={styles.crumb} onClick={() => go({ path: target })}>{segment}</button>}
          </li>
        })}
      </ol>
      {path && <button type="button" className="button quiet small" onClick={() => go({ path: parentPath(path) })}>
        <Icon name="up" size={16} />{t('treemap.up')}
      </button>}
    </nav>
    {view === 'changes' && diff.data && !gone && <p className="muted small">{t('treemap.comparing', {
      from: formatDateTime(diff.data.from_scan.finished_at),
      to: formatDateTime(diff.data.to_scan.finished_at),
    })}</p>}
    <div className={`${styles.body} ${side ? styles.split : ''}`}>
      <div className={styles.column}>
        {gone
          ? <LatestNotice onLatest={showLatest}><span>{errorMessage(current.error)}</span></LatestNotice>
          : missing
            ? <Notice>{t('treemap.missing')}</Notice>
            : current.error ? <Notice error>{errorMessage(current.error)}</Notice>
              : <Treemap items={items} label={t('treemap.label', { path: path || summary.directory.name })}
                openHint={t('treemap.openHint')} empty={current.isPending ? t('shell.loading') : t('treemap.empty')} />}
        {scans.isSuccess && !canCompare && !gone && <p className="muted small">
          {past ? t('errors.codes.no_previous_scan') : t('treemap.noComparison')}
        </p>}
      </div>
      {side && <div className={styles.column}>
        {rows.length > 0 && <div className={styles.tableWrap} role="region" tabIndex={0}
          aria-label={t('treemap.tableCaption', { path: path || summary.directory.name })}>
          <table>
            <caption className="visually-hidden">{t('treemap.tableCaption', { path: path || summary.directory.name })}</caption>
            <thead><tr>
              <th scope="col">{t('treemap.columns.name')}</th>
              <th scope="col" className="number">{view === 'changes' && !past ? t('treemap.columns.after') : t('treemap.columns.size')}</th>
              <th scope="col" className="number">{t('treemap.columns.share')}</th>
              {(view === 'changes' || diffByName.size > 0) && <th scope="col" className="number">{t('treemap.columns.change')}</th>}
            </tr></thead>
            <tbody>
              {rows.map(row => {
                const size = 'size' in row ? row.size : Math.max(row.before ?? 0, row.after ?? 0)
                const value = 'size' in row ? row.size : row.after
                const change = 'size' in row ? diffByName.get(row.name) : row
                return <tr key={row.path}>
                  <td>{row.has_children
                    ? <button type="button" className={styles.rowLink} onClick={() => go({ path: row.path })}>
                      <Icon name="folder" size={16} />{row.name}
                    </button>
                    : <span className={styles.rowName}><Icon name="folder" size={16} />{row.name}</span>}</td>
                  <td className="number">{value === null ? '—' : formatBytes(value)}</td>
                  <td className="number">{total ? formatPercent(size / total, undefined, 1) : '—'}</td>
                  {(view === 'changes' || diffByName.size > 0) && <td className="number">
                    {change ? <Delta before={change.before} after={change.after} /> : '—'}
                  </td>}
                </tr>
              })}
            </tbody>
          </table>
        </div>}
        {canCompare && diff.data && <div className={styles.biggest}>
          <h3>{t('treemap.biggest')}</h3>
          {diff.data.biggest.length === 0
            ? <p className="muted">{t('treemap.biggestEmpty')}</p>
            : <ol>
              {diff.data.biggest.map(change => {
                const [sizes] = changeLines(change.before, change.after)
                return <li key={change.path}>
                  <button type="button" className={styles.rowLink} onClick={() => go({ path: parentPath(change.path) })}>
                    {change.path}
                  </button>
                  <span className="muted small"> {sizes}</span>{' '}
                  <strong><Delta before={change.before} after={change.after} /></strong>
                </li>
              })}
            </ol>}
        </div>}
      </div>}
    </div>
  </section>
}
