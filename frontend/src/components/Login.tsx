import { useId, useState } from 'react'
import { api, errorMessage, queryClient } from '../api/client'
import { useI18n } from '../i18n'
import { Notice } from './ui'
import styles from './Login.module.css'

/** Sign-in with the single password of the installation (AUTH_PASSWORD). */
export function Login() {
  const { t } = useI18n()
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const id = useId()
  async function submit() {
    setBusy(true)
    setError(null)
    try {
      await api('/auth/login', 'POST', { password })
      queryClient.clear()
      queryClient.setQueryData(['auth'], { enabled: true, authenticated: true })
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }
  return <main className={styles.page} id="content">
    <form className={`card ${styles.card}`} onSubmit={event => { event.preventDefault(); void submit() }}>
      <img src="/icon.svg" alt="" width={48} height={48} />
      <h1>{t('auth.title')}</h1>
      <p className="muted">{t('auth.intro')}</p>
      <div className="field">
        <label htmlFor={id}>{t('auth.password')}</label>
        <input id={id} type="password" autoComplete="current-password" value={password} required
          onChange={event => setPassword(event.target.value)} />
      </div>
      {error !== null && <Notice error>{errorMessage(error)}</Notice>}
      <button type="submit" className="button" disabled={busy || !password}>{busy ? t('auth.signingIn') : t('auth.signIn')}</button>
    </form>
  </main>
}
