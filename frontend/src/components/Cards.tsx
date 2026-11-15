import type { DirectorySummaryOut, SettingsOut } from '../api/schema.gen'
import { useI18n } from '../i18n'
import { formatBytes, formatDateTime, formatDuration, formatNumber, formatPercent, formatRelative } from '../lib/format'
import { presetOf } from '../lib/schedule'
import { InfoTip } from './InfoTip'
import { Bar, levelOf } from './ui'
import styles from './Cards.module.css'

/** Volume, size, last scan and next scan of a directory. */
export function Cards({ summary, settings, now }: { summary: DirectorySummaryOut; settings?: SettingsOut; now: number }) {
  const { t, rich } = useI18n()
  const { capacity, last_scan: last, directory, next_run: next } = summary
  const warn = settings?.warn_percent ?? 80
  const critical = settings?.critical_percent ?? 90
  const preset = presetOf(directory.cron)
  return <div className={styles.grid}>
    <section className={`card ${styles.card}`} aria-labelledby="card-volume">
      <h2 id="card-volume" className={styles.title}>
        {t('cards.volume')}
        <InfoTip label={t('common.moreInfo')}>{rich('tips.volume', { code: chunks => <code>{chunks}</code> })}</InfoTip>
      </h2>
      {capacity ? <>
        <p className={styles.value}>{formatPercent(capacity.percent / 100)}</p>
        <Bar value={capacity.percent / 100} level={levelOf(capacity.percent, warn, critical)}
          label={t('cards.volumeUsed', { percent: formatPercent(capacity.percent / 100) })} />
        <p className={styles.detail}>{t('cards.volumeUsage', { used: formatBytes(capacity.used), total: formatBytes(capacity.total) })}</p>
        <p className={styles.detail}>{t('cards.volumeFree', { free: formatBytes(capacity.free) })}</p>
        <p className={`${styles.detail} muted`}>{t('cards.measured', { time: formatRelative(capacity.measured_at, now) })}</p>
        {capacity.shared_with.length > 0 && <p className={`${styles.detail} muted`}>
          {t('cards.sharedWith', { names: capacity.shared_with.map(other => other.name).join(', ') })}
        </p>}
      </> : <p className="muted">{t('cards.noVolume')}</p>}
    </section>
    <section className={`card ${styles.card}`} aria-labelledby="card-size">
      <h2 id="card-size" className={styles.title}>{t('cards.size')}</h2>
      {last ? <>
        <p className={styles.value}>{formatBytes(last.total_bytes)}</p>
        <p className={styles.detail}>{last.file_count !== null
          ? t('cards.files', { count: last.file_count })
          : t('cards.filesUnknown')}</p>
        <p className={styles.detail}>{t('cards.folders', { count: last.dir_count ?? 0 })}</p>
        <p className={`${styles.detail} muted`}>{t(`form.measures.${last.options.measure}.name`)}</p>
      </> : <p className="muted">—</p>}
    </section>
    <section className={`card ${styles.card}`} aria-labelledby="card-last">
      <h2 id="card-last" className={styles.title}>{t('cards.lastScan')}</h2>
      {last?.finished_at ? <>
        <p className={styles.value}>{formatRelative(last.finished_at, now)}</p>
        <p className={styles.detail}>{formatDateTime(last.finished_at)}</p>
        <p className={styles.detail}>{t('cards.took', { duration: formatDuration(last.duration_seconds) })}</p>
        <p className={`${styles.detail} muted`}>
          {t(`engines.${last.engine}.name`)} · {t(`form.paces.${last.options.pace}.name`)}
          {last.error_count > 0 && <> · {t('cards.errors', { count: last.error_count })}</>}
        </p>
      </> : <p className="muted">—</p>}
    </section>
    <section className={`card ${styles.card}`} aria-labelledby="card-next">
      <h2 id="card-next" className={styles.title}>{t('cards.nextScan')}</h2>
      {next && directory.enabled ? <>
        <p className={styles.value}>{formatRelative(next, now)}</p>
        <p className={styles.detail}>{formatDateTime(next)}</p>
      </> : <p className={styles.value}>{directory.enabled ? t('cards.manual') : t('cards.paused')}</p>}
      <p className={`${styles.detail} muted`}>
        {preset === 'custom' ? <code>{directory.cron}</code> : t(`schedule.presets.${preset}`)}
      </p>
      <p className={`${styles.detail} muted`}>{t('cards.engine', {
        engine: t(`engines.${directory.engine}.name`),
        threads: formatNumber(directory.threads),
      })}</p>
    </section>
  </div>
}
