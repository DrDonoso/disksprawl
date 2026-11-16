import { Navigate } from 'react-router'
import { errorMessage } from '../api/client'
import { useDirectories } from '../api/hooks'
import { useI18n } from '../i18n'
import { Icon } from './Icon'
import { useSettingsTarget } from './Shell'
import { Notice } from './ui'
import styles from './Home.module.css'

/** `/`: the first directory, or a welcome that leads to adding one. */
export function Home() {
  const { t } = useI18n()
  const directories = useDirectories()
  const [, openSettings] = useSettingsTarget()
  if (directories.isPending) return <p className="muted">{t('shell.loading')}</p>
  if (directories.error) return <Notice error>{errorMessage(directories.error)}</Notice>
  const first = directories.data.items[0]
  if (first) return <Navigate to={`/directories/${first.directory.id}${window.location.search}`} replace />
  return <section className={`card ${styles.welcome}`}>
    <img src="/icon.svg" alt="" width={64} height={64} />
    <h1>{t('home.title')}</h1>
    <p>{t('home.intro')}</p>
    <ol className={styles.steps}>
      <li>{t('home.steps.mount')}</li>
      <li>{t('home.steps.add')}</li>
      <li>{t('home.steps.scan')}</li>
    </ol>
    <button type="button" className="button" onClick={() => openSettings('new')}>
      <Icon name="plus" size={18} />{t('home.add')}
    </button>
  </section>
}

export function NotFound() {
  const { t } = useI18n()
  return <Notice error>{t('shell.notFound')}</Notice>
}
