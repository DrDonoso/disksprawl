import { Suspense, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, Outlet, useLocation, useSearchParams } from 'react-router'
import { api, queryClient } from '../api/client'
import { useAuthStatus, useDirectories } from '../api/hooks'
import { useI18n } from '../i18n'
import { WhatsNew } from '../whatsNew/WhatsNew'
import { Icon } from './Icon'
import { Preferences } from './Preferences'
import { SettingsDialog, type SettingsTarget } from './SettingsDialog'
import { Sidebar } from './Sidebar'
import styles from './Shell.module.css'

const COMPACT = '(max-width: 760px)'
const SIDEBAR_KEY = 'disksprawl_sidebar_hidden'

function storedHidden(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_KEY) === '1'
  } catch {
    return false
  }
}

function useCompact(): boolean {
  const [compact, setCompact] = useState(() => window.matchMedia?.(COMPACT).matches ?? false)
  useEffect(() => {
    const media = window.matchMedia?.(COMPACT)
    if (!media) return
    const change = () => setCompact(media.matches)
    media.addEventListener('change', change)
    return () => media.removeEventListener('change', change)
  }, [])
  return compact
}

/** `?settings=directories|general|notifications|new|<id>` opens the settings dialog (links and back button work). */
export function useSettingsTarget(): [SettingsTarget | null, (target: SettingsTarget | null) => void] {
  const [params, setParams] = useSearchParams()
  const raw = params.get('settings')
  const target: SettingsTarget | null = raw === 'directories' || raw === 'general' || raw === 'notifications' || raw === 'new'
    ? raw
    : raw && /^\d+$/.test(raw) ? Number(raw) : null
  const open = (next: SettingsTarget | null) => setParams(previous => {
    const copy = new URLSearchParams(previous)
    if (next === null) copy.delete('settings')
    else copy.set('settings', String(next))
    return copy
  })
  return [target, open]
}

export function Shell({ banner }: { banner?: ReactNode }) {
  const { t } = useI18n()
  const auth = useAuthStatus()
  const directories = useDirectories()
  const compact = useCompact()
  const location = useLocation()
  const [hidden, setHidden] = useState(storedHidden)
  const [overlayOpen, setOverlayOpen] = useState(false)
  const [settings, openSettings] = useSettingsTarget()
  const menuButton = useRef<HTMLButtonElement>(null)
  const visible = compact ? overlayOpen : !hidden

  useEffect(() => setOverlayOpen(false), [location.pathname, compact])

  function toggle() {
    if (compact) {
      setOverlayOpen(open => !open)
      return
    }
    const next = !hidden
    setHidden(next)
    try {
      localStorage.setItem(SIDEBAR_KEY, next ? '1' : '0')
    } catch {
      console.warn('The sidebar preference could not be stored.')
    }
  }

  async function signOut() {
    await api('/auth/logout', 'POST').catch(() => undefined)
    queryClient.clear()
    queryClient.setQueryData(['auth'], { enabled: true, authenticated: false })
  }

  return <div className={styles.app}>
    <a className="skip-link" href="#content">{t('shell.skipToContent')}</a>
    <header className={styles.topbar}>
      <button ref={menuButton} type="button" className="icon-button" aria-controls="directory-list" aria-expanded={visible}
        aria-label={t('shell.toggleMenu')} title={t('shell.toggleMenu')} onClick={toggle}>
        <Icon name={compact && visible ? 'close' : 'menu'} />
      </button>
      <Link to="/" className={styles.brand}>
        <img src="/icon.svg" alt="" width={26} height={26} />
        <span>DiskSprawl</span>
      </Link>
      <div className={styles.actions}>
        <Preferences />
        <WhatsNew />
        <button type="button" className="icon-button" aria-label={t('shell.settings')} title={t('shell.settings')}
          onClick={() => openSettings('directories')}>
          <Icon name="settings" />
        </button>
        {auth.data?.enabled && <button type="button" className="icon-button" aria-label={t('shell.signOut')}
          title={t('shell.signOut')} onClick={() => void signOut()}>
          <Icon name="logout" />
        </button>}
      </div>
    </header>
    {compact && visible && <div className={styles.backdrop} aria-hidden="true" onClick={() => setOverlayOpen(false)} />}
    <aside id="directory-list" className={`${styles.sidebar} ${compact ? styles.overlay : ''} ${visible ? '' : styles.closed}`}
      aria-label={t('sidebar.label')} inert={!visible} aria-hidden={!visible}>
      <Sidebar data={directories.data?.items} error={directories.error} onAdd={() => openSettings('new')} />
    </aside>
    <div className={`${styles.workspace} ${!compact && !hidden ? styles.withSidebar : ''}`}>
      <main id="content" tabIndex={-1} className={styles.main}>
        <Suspense fallback={<p className="muted">{t('shell.loading')}</p>}><Outlet /></Suspense>
      </main>
      {banner}
    </div>
    {settings !== null && <SettingsDialog target={settings} onNavigate={openSettings} onClose={() => openSettings(null)} />}
  </div>
}
