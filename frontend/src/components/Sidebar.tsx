import { NavLink } from 'react-router'
import { errorMessage } from '../api/client'
import type { DirectorySummaryOut } from '../api/schema.gen'
import { useSettings } from '../api/hooks'
import { useI18n } from '../i18n'
import { formatBytes, formatPercent, formatRelative } from '../lib/format'
import { useNow } from '../lib/hooks'
import { Icon } from './Icon'
import { Bar, levelOf } from './ui'
import styles from './Sidebar.module.css'

function Status({ summary, now }: { summary: DirectorySummaryOut; now: number }) {
  const { t } = useI18n()
  const { progress, last_scan: last, last_attempt: attempt } = summary
  if (progress?.status === 'queued') return <span className={styles.status}>{t('sidebar.queued')}</span>
  if (progress) {
    const label = progress.percent !== null
      ? t('sidebar.scanningPercent', { percent: formatPercent(progress.percent) })
      : t('sidebar.scanning')
    return <span className={styles.progress}>
      <span className={styles.status}>{label}</span>
      <Bar value={progress.percent} label={label} level="progress" size="thin" />
    </span>
  }
  if (attempt) {
    return <span className={`${styles.status} ${styles.failed}`}>
      <Icon name="warning" size={14} />{t(`sidebar.attempt.${attempt.status as 'failed' | 'cancelled' | 'interrupted'}`)}
    </span>
  }
  if (!last?.finished_at) return <span className={styles.status}>{t('sidebar.never')}</span>
  return <span className={styles.status}>
    {t('sidebar.updated', { time: formatRelative(last.finished_at, now, { style: 'short' }) })}
  </span>
}

/** The directories, each with its size, its volume and how fresh its data is. */
export function Sidebar({ data, error, onAdd }: { data?: DirectorySummaryOut[]; error: unknown; onAdd: () => void }) {
  const { t } = useI18n()
  const settings = useSettings()
  const now = useNow()
  const warn = settings.data?.warn_percent ?? 80
  const critical = settings.data?.critical_percent ?? 90
  return <nav className={styles.nav} aria-label={t('sidebar.label')}>
    <h2 className={styles.title}>{t('sidebar.title')}</h2>
    {error ? <p className={styles.empty} role="alert">{errorMessage(error)}</p> : null}
    {data && data.length === 0 && <div className={styles.empty}>
      <p>{t('sidebar.empty')}</p>
      <button type="button" className="button small" onClick={onAdd}><Icon name="plus" size={16} />{t('sidebar.add')}</button>
    </div>}
    <ul className={styles.list}>
      {data?.map(summary => {
        const { directory, capacity, last_scan: last } = summary
        const share = capacity ? capacity.percent / 100 : null
        return <li key={directory.id}>
          <NavLink to={`/directories/${directory.id}`} className={({ isActive }) => `${styles.item} ${isActive ? styles.active : ''}`}>
            <span className={styles.row}>
              <span className={styles.name}>{directory.name}</span>
              <span className={styles.size}>{last ? formatBytes(last.total_bytes) : ''}</span>
            </span>
            {capacity && share !== null && <span className={styles.volume}>
              <Bar value={share} level={levelOf(capacity.percent, warn, critical)} size="thin"
                label={t('sidebar.volume', { percent: formatPercent(share) })} />
              <span className={styles.percent}>{formatPercent(share)}</span>
            </span>}
            <Status summary={summary} now={now} />
          </NavLink>
        </li>
      })}
    </ul>
  </nav>
}
