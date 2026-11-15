import { useState } from 'react'
import { ToggleButton, ToggleButtonGroup } from 'react-aria-components'
import { useCapacityHistory, useScans } from '../api/hooks'
import type { ScanOut, SettingsOut } from '../api/schema.gen'
import { useI18n } from '../i18n'
import { formatBytes, formatDateTime, formatDuration, formatPercent } from '../lib/format'
import { failureReason } from '../lib/messages'
import { LineChart, type Point } from './LineChart'
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

/** Size of the directory per scan, use of its volume, and the latest scans. */
export function History({ directoryId, settings }: { directoryId: number; settings?: SettingsOut }) {
  const { t } = useI18n()
  const [days, setDays] = useState<Range>(90)
  const scans = useScans(directoryId, days)
  const capacity = useCapacityHistory(directoryId, days)
  const completed = (scans.data?.items ?? []).filter(scan => scan.status === 'completed' && scan.finished_at && scan.total_bytes !== null)
  const sizePoints: Point[] = completed.slice().reverse().map(scan => ({
    time: new Date(scan.finished_at as string).getTime(),
    value: scan.total_bytes as number,
    label: `${formatDateTime(scan.finished_at)}: ${formatBytes(scan.total_bytes)}`,
  }))
  const volumePoints: Point[] = (capacity.data?.points ?? []).map(point => ({
    time: new Date(point.measured_at).getTime(),
    value: point.percent,
    label: `${formatDateTime(point.measured_at)}: ${formatPercent(point.percent / 100, undefined, 1)}`,
  }))
  const recent = (scans.data?.items ?? []).slice(0, 10)
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
    <div className={styles.charts}>
      <figure>
        <figcaption>{t('history.size')}</figcaption>
        <LineChart points={sizePoints} label={t('history.size')} format={value => formatBytes(value)} />
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
      <h3>{t('history.recent')}</h3>
      <table>
        <thead><tr>
          <th scope="col">{t('history.columns.date')}</th>
          <th scope="col">{t('history.columns.status')}</th>
          <th scope="col">{t('history.columns.trigger')}</th>
          <th scope="col">{t('history.columns.engine')}</th>
          <th scope="col" className="number">{t('history.columns.duration')}</th>
          <th scope="col" className="number">{t('history.columns.size')}</th>
        </tr></thead>
        <tbody>
          {recent.map(scan => <tr key={scan.id}>
            <td className="nowrap">{formatDateTime(scan.finished_at ?? scan.started_at ?? scan.queued_at)}</td>
            <td><StatusCell scan={scan} /></td>
            <td>{t(`history.triggers.${scan.trigger}`)}</td>
            <td>{t(`engines.${scan.engine}.name`)}</td>
            <td className="number">{formatDuration(scan.duration_seconds)}</td>
            <td className="number">{scan.total_bytes === null ? '—' : formatBytes(scan.total_bytes)}</td>
          </tr>)}
        </tbody>
      </table>
    </div>}
  </section>
}
