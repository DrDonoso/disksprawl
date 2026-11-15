import { useEffect, useState } from 'react'
import { useBrowse } from '../api/hooks'
import { errorMessage } from '../api/client'
import { useI18n } from '../i18n'
import { Icon } from './Icon'
import { Notice } from './ui'
import styles from './DirectoryForm.module.css'

/** Navigates the folders below the scan root (/data) and returns the chosen one. */
export function FolderPicker({ start, onPick, onCancel }: { start: string | null; onPick: (path: string) => void; onCancel: () => void }) {
  const { t } = useI18n()
  const [path, setPath] = useState<string | null>(start)
  const browse = useBrowse(path)
  const data = browse.data
  const failed = Boolean(browse.error)
  // A typed path that cannot be browsed: start from the root instead.
  useEffect(() => {
    if (failed && path !== null) setPath(null)
  }, [failed, path])
  return <div className={styles.picker} role="group" aria-label={t('picker.label')}>
    <div className={styles.pickerHead}>
      <button type="button" className="icon-button" disabled={!data?.parent} aria-label={t('picker.up')}
        onClick={() => setPath(data?.parent ?? null)}>
        <Icon name="up" size={18} />
      </button>
      <code className={styles.pickerPath}>{data?.path ?? '…'}</code>
    </div>
    {browse.error && path === null && <Notice error>{errorMessage(browse.error)}</Notice>}
    {data && data.entries.length === 0 && <p className="muted small">{t('picker.empty')}</p>}
    {data && data.entries.length > 0 && <ul className={styles.entries}>
      {data.entries.map(entry => <li key={entry.path}>
        <button type="button" className={styles.entry} onClick={() => setPath(entry.path)}>
          <Icon name="folder" size={16} />{entry.name}
        </button>
      </li>)}
    </ul>}
    <div className={styles.pickerActions}>
      <button type="button" className="button small" disabled={!data} onClick={() => data && onPick(data.path)}>
        {t('picker.choose')}
      </button>
      <button type="button" className="button secondary small" onClick={onCancel}>{t('common.cancel')}</button>
    </div>
  </div>
}
