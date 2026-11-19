import { useId, useState } from 'react'
import { Switch } from 'react-aria-components'
import { errorMessage } from '../api/client'
import { useI18n } from '../i18n'
import { usePush, type PushChoice } from '../push/PushContext'
import { Notice } from './ui'
import formStyles from './DirectoryForm.module.css'
import styles from './NotificationSettings.module.css'

/** The choices of a device, in the order shown (src/disksprawl/notifications.py). */
const CHOICES: readonly PushChoice[] = ['manual_scans', 'scheduled_scans', 'failed_scans', 'capacity']

function Toggle({ label, isSelected, isDisabled, describedBy, onChange }: {
  label: string
  isSelected: boolean
  isDisabled?: boolean
  describedBy?: string
  onChange: (value: boolean) => void
}) {
  return <Switch isSelected={isSelected} isDisabled={isDisabled} onChange={onChange} aria-describedby={describedBy}
    className={formStyles.switch}>
    <span className={formStyles.track} aria-hidden="true"><span className={formStyles.thumb} /></span>
    {label}
  </Switch>
}

/**
 * ⚙ Settings → Notifications: turns them on for this browser or phone, and chooses what it is
 * told; or says why they cannot be turned on here (the demo, a page without HTTPS, an iPhone in
 * Safari, a browser without them, blocked notifications).
 */
export function NotificationSettings() {
  const { t } = useI18n()
  const push = usePush()
  const [error, setError] = useState<string | null>(null)
  const id = useId()
  if (!push) return null
  const { status, preferences, busy, failed } = push

  function failure(reason: unknown) {
    setError(errorMessage(reason))
  }

  function toggle(on: boolean) {
    if (!push) return
    setError(null)
    const change = on ? push.enable().then(() => undefined) : push.disable()
    change.catch(failure)
  }

  function choose(choice: PushChoice, on: boolean) {
    if (!push) return
    setError(null)
    push.choose(choice, on).catch(failure)
  }

  if (status !== 'on' && status !== 'off') {
    return <div className={formStyles.form}>
      <p className="muted">{t('notifications.intro')}</p>
      <Notice>{t(`notifications.unavailable.${status}`)}</Notice>
    </div>
  }

  return <div className={formStyles.form}>
    <p className="muted">{t('notifications.intro')}</p>
    <div className={styles.choice}>
      <Toggle label={t('notifications.device')} isSelected={status === 'on'} isDisabled={busy}
        describedBy={`${id}-device`} onChange={toggle} />
      <p id={`${id}-device`} className="muted small">{t('notifications.deviceHint')}</p>
    </div>
    {error && <Notice error>{error}</Notice>}
    {status === 'on' && failed && !preferences && <div className={styles.failed}>
      <Notice error>{t('notifications.loadError')}</Notice>
      <button type="button" className="button secondary small" onClick={push.retry}>{t('notifications.retry')}</button>
    </div>}
    {status === 'on' && preferences && <fieldset className={formStyles.section}>
      <legend>{t('notifications.choices')}</legend>
      {CHOICES.map(choice => <div key={choice} className={styles.choice}>
        <Toggle label={t(`notifications.kinds.${choice}.label`)} isSelected={preferences[choice]}
          describedBy={`${id}-${choice}`} onChange={on => choose(choice, on)} />
        <p id={`${id}-${choice}`} className="muted small">{t(`notifications.kinds.${choice}.description`)}</p>
      </div>)}
      <p className="muted small">{t('notifications.perDevice')}</p>
    </fieldset>}
    {status === 'on' && !preferences && !failed && <p className="muted">{t('shell.loading')}</p>}
  </div>
}
