import { useState } from 'react'
import { ToggleButton, ToggleButtonGroup } from 'react-aria-components'
import { errorMessage } from '../api/client'
import { useCapacityHistory, useDownloadLog, useScans } from '../api/hooks'
import type { ScanOut, SettingsOut } from '../api/schema.gen'
import { useI18n } from '../i18n'
import { formatBytes, formatDateTime, formatDuration, formatPercent } from '../lib/format'
import { failureReason } from '../lib/messages'
import { Icon } from './Icon'
import { LineChart, type Point } from './LineChart'
import { Notice } from './ui'
import styles from './History.module.css'

const RANGES = [30, 90, 365] as const
type Range = (typeof RANGES)[number]

function StatusCell({ scan }: { scan: ScanOut }) {
  const { t } = useI18n()
  const label = t(`history.statuses.${scan.status}`)
  if (scan.status === 'completed' && scan.error_count) {
    return <span className={styles.warn}>{label} · {t('cards.errors', { count: scan.error_count })}</span>
  }
  if (scan.status === 'failed') {
    return <span className={styles.bad} title={failureReason(scan.message) ?? undefined}>{label}</span>
  }
  return <span className={scan.status === 'completed' ? '' : 'muted'}>{label}</span>
}

/**
 * Size of the directory per scan, use of its volume, and the latest scans. A scan that keeps its
 * folders opens in the treemap from its point in the size chart or from its date; each scan that
 * started downloads its log.
 */
export function History({ directoryId, settings, shown, onOpen }: {
  directoryId: number
  settings?: SettingsOut
  /** The scan chosen for the treemap; null when it shows the latest one. */
  shown: number | null
  /** Shows a scan in the treemap; null, the latest one. */
  onOpen: (scan: number | null) => void
}) {
  const { t } = useI18n()
  const [days, setDays] = useState<Range>(90)
  const scans = useScans(directoryId, days)
  const capacity = useCapacityHistory(directoryId, days)
  const downloadLog = useDownloadLog()
  const items = scans.data?.items ?? []
  const completed = items.filter(scan => scan.status === 'completed' && scan.finished_at && scan.total_bytes !== null)
  // Choosing the latest scan with folders goes back to the default view, which follows new scans.
  const latest = items.find(scan => scan.status === 'completed' && scan.has_detail)
  const open = (id: number) => onOpen(id === latest?.id ? null : id)
  const sizePoints: Point[] = completed.slice().reverse().map(scan => ({
    time: new Date(scan.finished_at as string).getTime(),
    value: scan.total_bytes as number,
    label: `${formatDateTime(scan.finished_at)}: ${formatBytes(scan.total_bytes)}`,
    id: scan.id,
    openable: scan.has_detail,
  }))
  const volumePoints: Point[] = (capacity.data?.points ?? []).map(point => ({
    time: new Date(point.measured_at).getTime(),
    value: point.percent,
    label: `${formatDateTime(point.measured_at)}: ${formatPercent(point.percent / 100, undefined, 1)}`,
  }))
  const recent = items.slice(0, 10)
  return <section className={`card ${styles.history}`} aria-labelledby="history-title">
    <div className={styles.head}>
      <h2 id="history-title">{t('history.title')}</h2>
      <ToggleButtonGroup aria-label={t('history.range')} selectionMode="single" disallowEmptySelection
        selectedKeys={[String(days)]} className={styles.toggle}
        onSelectionChange={keys => setDays(Number([...keys][0]) as Range)}>
        {RANGES.map(range => <ToggleButton key={range} id={String(range)} className={styles.toggleButton}>
          {t('history.days', { count: range })}
        </ToggleButton>)}
      </ToggleButtonGroup>
    </div>
    <div className={`${styles.body} ${recent.length > 0 ? styles.split : ''}`}>
      <div className={styles.charts}>
        <figure>
          <figcaption>{t('history.size')}</figcaption>
          <LineChart points={sizePoints} label={t('history.size')} format={value => formatBytes(value)}
            onOpen={point => { if (point.id !== undefined) open(point.id) }} current={shown} />
        </figure>
        <figure>
          <figcaption>{t('history.volume')}</figcaption>
          <LineChart points={volumePoints} label={t('history.volume')} min={0} max={100} color="var(--chart-volume)"
            format={value => formatPercent(value / 100)}
            thresholds={settings ? [
              { value: settings.warn_percent, color: 'var(--warn-fill)' },
              { value: settings.critical_percent, color: 'var(--danger-fill)' },
            ] : []} />
        </figure>
      </div>
      {recent.length > 0 && <div className={styles.tableWrap}>
        <h3 id="history-recent">{t('history.recent')}</h3>
        <div className={styles.scroll} role="region" tabIndex={0} aria-labelledby="history-recent">
          <table>
            <thead><tr>
              <th scope="col">{t('history.columns.date')}</th>
              <th scope="col">{t('history.columns.status')}</th>
              <th scope="col">{t('history.columns.trigger')}</th>
              <th scope="col">{t('history.columns.engine')}</th>
              <th scope="col" className="number">{t('history.columns.duration')}</th>
              <th scope="col" className="number">{t('history.columns.size')}</th>
              <th scope="col" className={styles.logColumn}>{t('history.columns.log')}</th>
            </tr></thead>
            <tbody>
              {recent.map(scan => {
                const date = formatDateTime(scan.finished_at ?? scan.started_at ?? scan.queued_at)
                const current = scan.id === shown
                return <tr key={scan.id} className={current ? styles.current : undefined}>
                  <td className="nowrap">{scan.status === 'completed' && scan.has_detail
                    ? <button type="button" className={styles.open} aria-current={current || undefined} onClick={() => open(scan.id)}>
                      {date} <span className="visually-hidden">({t('history.openHint')})</span>
                    </button>
                    : date}</td>
                  <td><StatusCell scan={scan} /></td>
                  <td>{t(`history.triggers.${scan.trigger}`)}</td>
                  <td>{t(`engines.${scan.engine}.name`)}</td>
                  <td className="number">{formatDuration(scan.duration_seconds)}</td>
                  <td className="number">{scan.total_bytes === null ? '—' : formatBytes(scan.total_bytes)}</td>
                  <td className={styles.logColumn}>{scan.has_log && <button type="button" className={styles.log}
                    title={t('history.log')} aria-label={t('history.logOf', { date })}
                    disabled={downloadLog.isPending && downloadLog.variables === scan.id}
                    onClick={() => downloadLog.mutate(scan.id)}>
                    <Icon name="download" size={16} />
                  </button>}</td>
                </tr>
              })}
            </tbody>
          </table>
        </div>
        {downloadLog.error && <Notice error>{errorMessage(downloadLog.error)}</Notice>}
      </div>}
    </div>
    {latest && settings && <p className="muted small">{t('history.help', { count: settings.keep_scans })}</p>}
  </section>
}
