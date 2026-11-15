import { useMemo } from 'react'
import { useSearchParams } from 'react-router'
import { ToggleButton, ToggleButtonGroup } from 'react-aria-components'
import { ApiError, errorMessage } from '../api/client'
import { useDiff, useScans, useTree } from '../api/hooks'
import type { DiffChildOut, DirectorySummaryOut, TreeChildOut } from '../api/schema.gen'
import { useI18n } from '../i18n'
import { changeBlocks, changeLines, parentPath, sizeBlocks, type Block } from '../lib/blocks'
import { formatBytes, formatDateTime, formatDelta, formatPercent } from '../lib/format'
import { useTheme } from '../lib/hooks'
import { Icon } from './Icon'
import { Treemap, type TreemapItem } from './Treemap'
import { Notice } from './ui'
import styles from './Explorer.module.css'

type View = 'size' | 'changes'

function Delta({ before, after }: { before: number | null; after: number | null }) {
  const { t } = useI18n()
  if (before === null) return <span className="delta-up">{t('treemap.new')}</span>
  if (after === null) return <span className="delta-down">{t('treemap.deleted')}</span>
  const delta = after - before
  if (!delta) return <span className="muted">0</span>
  return <span className={delta > 0 ? 'delta-up' : 'delta-down'}>{formatDelta(delta)}</span>
}

/** The folders of a directory: treemap, breadcrumbs, the table of the level and the biggest changes. */
export function Explorer({ summary }: { summary: DirectorySummaryOut }) {
  const { t } = useI18n()
  const theme = useTheme()
  const id = summary.directory.id
  const [params, setParams] = useSearchParams()
  const path = params.get('path') ?? ''
  const fromParam = Number(params.get('from'))
  const from = Number.isInteger(fromParam) && fromParam > 0 ? fromParam : null
  const scans = useScans(id, 3650)
  const detailed = useMemo(
    () => (scans.data?.items ?? []).filter(scan => scan.status === 'completed' && scan.has_detail),
    [scans.data],
  )
  const canCompare = detailed.length >= 2
  const view: View = params.get('view') === 'changes' && canCompare ? 'changes' : 'size'
  const tree = useTree(id, path, null, view === 'size')
  const diff = useDiff(id, path, from, null, canCompare)

  function go(changes: { path?: string; view?: View; from?: number | null }) {
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
  const missing = current.error instanceof ApiError && current.error.status === 404
  const diffByName = new Map((diff.data?.path === path ? diff.data.children : []).map(child => [child.name, child]))
  const total = view === 'changes'
    ? Math.max(diff.data?.before ?? 0, diff.data?.after ?? 0)
    : tree.data?.size ?? 0
  const rows: (TreeChildOut | DiffChildOut)[] = view === 'changes' ? diff.data?.children ?? [] : tree.data?.children ?? []
  const latest = detailed[0]

  return <section className={`card ${styles.explorer}`} aria-labelledby="explorer-title">
    <div className={styles.toolbar}>
      <h2 id="explorer-title">{t('treemap.title')}</h2>
      <ToggleButtonGroup aria-label={t('treemap.viewLabel')} selectionMode="single" disallowEmptySelection
        selectedKeys={[view]} className={styles.toggle}
        onSelectionChange={keys => go({ view: ([...keys][0] as View) ?? 'size' })}>
        <ToggleButton id="size" className={styles.toggleButton}>{t('treemap.size')}</ToggleButton>
        <ToggleButton id="changes" className={styles.toggleButton} isDisabled={!canCompare}>{t('treemap.changes')}</ToggleButton>
      </ToggleButtonGroup>
      {view === 'changes' && <label className={styles.compare}>
        <span>{t('treemap.compareWith')}</span>
        <select className="input" value={from ?? ''} onChange={event => go({ from: Number(event.target.value) || null })}>
          <option value="">{t('treemap.previousScan')}</option>
          {detailed.filter(scan => scan.id !== latest?.id).map(scan => <option key={scan.id} value={scan.id}>
            {formatDateTime(scan.finished_at)} · {formatBytes(scan.total_bytes)}
          </option>)}
        </select>
      </label>}
    </div>
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
    {view === 'changes' && diff.data && <p className="muted small">{t('treemap.comparing', {
      from: formatDateTime(diff.data.from_scan.finished_at),
      to: formatDateTime(diff.data.to_scan.finished_at),
    })}</p>}
    {missing
      ? <Notice>{t('treemap.missing')}</Notice>
      : current.error ? <Notice error>{errorMessage(current.error)}</Notice>
        : <Treemap items={items} label={t('treemap.label', { path: path || summary.directory.name })}
          empty={current.isPending ? t('shell.loading') : t('treemap.empty')} />}
    {!canCompare && <p className="muted small">{t('treemap.noComparison')}</p>}
    {rows.length > 0 && <div className={styles.tableWrap}>
      <table>
        <caption className="visually-hidden">{t('treemap.tableCaption', { path: path || summary.directory.name })}</caption>
        <thead><tr>
          <th scope="col">{t('treemap.columns.name')}</th>
          <th scope="col" className="number">{view === 'changes' ? t('treemap.columns.after') : t('treemap.columns.size')}</th>
          <th scope="col" className="number">{t('treemap.columns.share')}</th>
          {(view === 'changes' || diffByName.size > 0) && <th scope="col" className="number">{t('treemap.columns.change')}</th>}
        </tr></thead>
        <tbody>
          {rows.map(row => {
            const size = 'size' in row ? row.size : Math.max(row.before ?? 0, row.after ?? 0)
            const shown = 'size' in row ? row.size : row.after
            const change = 'size' in row ? diffByName.get(row.name) : row
            return <tr key={row.path}>
              <td>{row.has_children
                ? <button type="button" className={styles.rowLink} onClick={() => go({ path: row.path })}>
                  <Icon name="folder" size={16} />{row.name}
                </button>
                : <span className={styles.rowName}><Icon name="folder" size={16} />{row.name}</span>}</td>
              <td className="number">{shown === null ? '—' : formatBytes(shown)}</td>
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
  </section>
}
