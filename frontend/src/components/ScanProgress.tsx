import { useCancelScan } from '../api/hooks'
import { errorMessage } from '../api/client'
import type { ProgressOut } from '../api/schema.gen'
import { useI18n } from '../i18n'
import { formatBytes, formatDuration, formatNumber, formatOrdinal, formatPercent } from '../lib/format'
import { Bar, Notice } from './ui'
import styles from './ScanProgress.module.css'

/** The scan in progress: phase, an estimate when there is one, live counters and Cancel. */
export function ScanProgress({ progress }: { progress: ProgressOut }) {
  const { t } = useI18n()
  const cancel = useCancelScan()
  const queued = progress.status === 'queued'
  const phase = queued
    ? t('progress.queued', { position: formatOrdinal(progress.position ?? 1) })
    : progress.phase === 'saving' ? t('progress.saving')
      : progress.phase === 'finishing' ? t('progress.finishing') : t('progress.scanning')
  const percent = progress.percent
  const estimate = percent !== null
    ? [
      t('progress.percent', { percent: formatPercent(percent) }),
      progress.remaining_seconds !== null ? t('progress.remaining', { time: formatDuration(progress.remaining_seconds) }) : null,
    ].filter(Boolean).join(' · ')
    : null
  const previous = progress.previous_duration_seconds !== null
    ? t('progress.lastTook', { time: formatDuration(progress.previous_duration_seconds) })
    : null
  return <section className={`card ${styles.panel}`} aria-label={t('progress.label')}>
    <div className={styles.head}>
      <h2 className={styles.phase} aria-live="polite">{phase}</h2>
      <button type="button" className="button danger small" disabled={cancel.isPending}
        onClick={() => cancel.mutate({ id: progress.scan_id, directory: progress.directory_id })}>
        {cancel.isPending ? t('progress.cancelling') : t('progress.cancel')}
      </button>
    </div>
    {!queued && <>
      <Bar value={progress.phase === 'scanning' ? percent : null} level="progress" label={phase}
        valueText={estimate ?? t('progress.elapsed', { time: formatDuration(progress.elapsed_seconds) })} />
      <p className={styles.estimate}>
        {estimate && <><strong>{estimate}</strong> <span className="muted">({t('common.approximate')})</span> · </>}
        {t('progress.elapsed', { time: formatDuration(progress.elapsed_seconds) })}
        {!estimate && previous && <> · {previous}</>}
      </p>
      {progress.counters
        ? <dl className={styles.counters}>
          <div><dt>{t('progress.folders')}</dt><dd>{formatNumber(progress.dirs)}</dd></div>
          {progress.files !== null && <div><dt>{t('progress.files')}</dt><dd>{formatNumber(progress.files)}</dd></div>}
          <div><dt>{t('progress.read')}</dt><dd>{formatBytes(progress.bytes)}</dd></div>
        </dl>
        : <p className="muted small">{t('progress.noCounters', { engine: t(`engines.${progress.engine}.name`) })}</p>}
      {progress.current !== null && progress.phase === 'scanning' && <p className={styles.current}>
        <span className="muted">{t('progress.current')}</span> <code>{progress.current || '/'}</code>
      </p>}
    </>}
    <p className="muted small">
      {t('progress.settings', {
        engine: t(`engines.${progress.engine}.name`),
        pace: t(`form.paces.${progress.pace}.name`),
        threads: t('progress.threads', { count: progress.threads }),
      })}
    </p>
    {cancel.error && <Notice error>{errorMessage(cancel.error)}</Notice>}
  </section>
}
