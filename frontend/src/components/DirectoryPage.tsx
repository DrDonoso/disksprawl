import { useCallback, useEffect, useRef } from 'react'
import { useParams, useSearchParams } from 'react-router'
import { ApiError, errorMessage } from '../api/client'
import { useDownloadLog, useOverview, useRefreshDirectory, useScanNow, useSettings } from '../api/hooks'
import type { DirectorySummaryOut, ScanOut } from '../api/schema.gen'
import { useI18n } from '../i18n'
import { failureReason } from '../lib/messages'
import { formatDateTime, formatDuration, formatRelative, formatShortDateTime } from '../lib/format'
import { useNow } from '../lib/hooks'
import { Cards } from './Cards'
import { Explorer, scanParam } from './Explorer'
import { History } from './History'
import { Icon } from './Icon'
import { ScanProgress } from './ScanProgress'
import { Notice } from './ui'
import styles from './DirectoryPage.module.css'

export function ScanErrors({ scan }: { scan: ScanOut }) {
  const { t } = useI18n()
  const downloadLog = useDownloadLog()
  if (!scan.error_count) return null
  return <details className={styles.errors}>
    <summary>{t('directory.withErrors', { count: scan.error_count })}</summary>
    <p className="muted small">{t('directory.errorsHelp')}</p>
    <ul>
      {scan.error_sample.map((error, index) => <li key={index}>
        <code>{error.path || '/'}</code> — {error.message}
      </li>)}
    </ul>
    {scan.error_count > scan.error_sample.length && <p className="muted small">
      {t('directory.moreErrors', { count: scan.error_count - scan.error_sample.length })}
    </p>}
    {scan.has_log && <button type="button" className={`button secondary small ${styles.errorsLog}`}
      disabled={downloadLog.isPending} onClick={() => downloadLog.mutate(scan.id)}>
      <Icon name="download" size={16} />{t('directory.errorsLog')}
    </button>}
    {downloadLog.error && <Notice error>{errorMessage(downloadLog.error)}</Notice>}
  </details>
}

/** "Updated 3 hours ago", or why the data on screen is older than the last attempt. */
export function Freshness({ summary, now }: { summary: DirectorySummaryOut; now: number }) {
  const { t } = useI18n()
  const { last_scan: last, last_attempt: attempt } = summary
  if (attempt) {
    const status = attempt.status as 'failed' | 'cancelled' | 'interrupted'
    const time = formatRelative(attempt.finished_at, now)
    const reason = status === 'failed' ? failureReason(attempt.message) : null
    return <p className={`${styles.freshness} ${styles.warning}`} role="status">
      <Icon name="warning" size={16} />
      <span>
        {last?.finished_at
          ? t(`directory.attempt.${status}.withData`, { time, date: formatShortDateTime(last.finished_at) })
          : t(`directory.attempt.${status}.withoutData`, { time })}
        {reason && <> {t('directory.reason', { reason })}</>}
      </span>
    </p>
  }
  if (!last?.finished_at) return <p className={styles.freshness}>{t('directory.never')}</p>
  const details = t('directory.updatedDetails', {
    date: formatDateTime(last.finished_at),
    duration: formatDuration(last.duration_seconds),
    engine: t(`engines.${last.engine}.name`),
  })
  return <p className={styles.freshness}>
    <time dateTime={last.finished_at} title={details}>{t('directory.updated', { time: formatRelative(last.finished_at, now) })}</time>
    <span className="visually-hidden"> ({details})</span>
  </p>
}

export function DirectoryPage() {
  const { t } = useI18n()
  const params = useParams()
  const id = Number(params.id)
  const overview = useOverview(id)
  const settings = useSettings()
  const scanNow = useScanNow()
  const refresh = useRefreshDirectory()
  const now = useNow()
  const active = Boolean(overview.data?.progress)
  const wasActive = useRef(active)
  const [search, setSearch] = useSearchParams()
  const contents = useRef<HTMLElement>(null)

  // A scan chosen in the history (null: the latest) goes to the treemap, and so do the page and
  // the keyboard: the treemap is above the history.
  const openScan = useCallback((scan: number | null) => {
    setSearch(previous => {
      const next = new URLSearchParams(previous)
      if (scan === null) next.delete('scan')
      else next.set('scan', String(scan))
      next.delete('from')
      return next
    })
    const section = contents.current
    if (!section) return
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    section.scrollIntoView?.({ block: 'start', behavior: still ? 'auto' : 'smooth' })
    section.focus({ preventScroll: true })
  }, [setSearch])

  // When a scan ends, everything on screen reloads with the new data.
  useEffect(() => {
    if (wasActive.current && !active) void refresh(id)
    wasActive.current = active
  }, [active, id, refresh])

  useEffect(() => {
    if (overview.data) document.title = `${overview.data.directory.name} · DiskSprawl`
    return () => { document.title = 'DiskSprawl' }
  }, [overview.data])

  if (overview.isPending) return <p className="muted">{t('shell.loading')}</p>
  if (overview.error) {
    const missing = overview.error instanceof ApiError && overview.error.status === 404
    return <Notice error>{missing ? t('directory.notFound') : errorMessage(overview.error)}</Notice>
  }
  const summary = overview.data
  const { directory, progress, last_scan: last } = summary
  const busy = Boolean(progress) || scanNow.isPending
  const scanLabel = progress?.status === 'queued' ? t('directory.queued') : progress ? t('directory.scanning') : t('directory.scanNow')
  return <div className={styles.page}>
    <header className={styles.header}>
      <div className={styles.titles}>
        <h1>{directory.name}</h1>
        <p className={`mono ${styles.path}`}>{directory.path}</p>
        <Freshness summary={summary} now={now} />
        {last && <ScanErrors scan={last} />}
      </div>
      <button type="button" className="button" disabled={busy} onClick={() => scanNow.mutate(id)}>
        <Icon name="scan" size={18} />{scanLabel}
      </button>
    </header>
    {scanNow.error && <Notice error>{errorMessage(scanNow.error)}</Notice>}
    {progress && <ScanProgress progress={progress} />}
    <Cards summary={summary} settings={settings.data} now={now} />
    {last
      ? <Explorer summary={summary} ref={contents} />
      : !progress && <section className={`card ${styles.empty}`}>
        <h2>{t('directory.emptyTitle')}</h2>
        <p className="muted">{t('directory.emptyText')}</p>
      </section>}
    <History directoryId={id} settings={settings.data} shown={scanParam(search, 'scan')} onOpen={openScan} />
  </div>
}
