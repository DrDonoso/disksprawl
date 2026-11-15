import { Dialog, Heading, Modal, ModalOverlay, Tab, TabList, TabPanel, Tabs } from 'react-aria-components'
import { useNavigate } from 'react-router'
import { errorMessage } from '../api/client'
import { useDirectories, useOrderDirectories } from '../api/hooks'
import { useI18n } from '../i18n'
import { presetOf } from '../lib/schedule'
import { DirectoryForm } from './DirectoryForm'
import { GeneralSettings } from './GeneralSettings'
import { Icon } from './Icon'
import { Notice } from './ui'
import styles from './SettingsDialog.module.css'

/** What the settings dialog shows: a tab, the form of a new directory, or of an existing one. */
export type SettingsTarget = 'directories' | 'general' | 'new' | number

function DirectoryList({ onNavigate }: { onNavigate: (target: SettingsTarget) => void }) {
  const { t } = useI18n()
  const directories = useDirectories()
  const order = useOrderDirectories()
  const items = directories.data?.items ?? []
  function move(index: number, offset: number) {
    const ids = items.map(item => item.directory.id)
    const [moved] = ids.splice(index, 1)
    ids.splice(index + offset, 0, moved)
    order.mutate(ids)
  }
  return <div className={styles.list}>
    <div className={styles.listHead}>
      <p className="muted">{t('settings.directoriesIntro')}</p>
      <button type="button" className="button" onClick={() => onNavigate('new')}><Icon name="plus" size={16} />{t('settings.add')}</button>
    </div>
    {directories.error && <Notice error>{errorMessage(directories.error)}</Notice>}
    {order.error && <Notice error>{errorMessage(order.error)}</Notice>}
    {items.length === 0 && directories.data && <p className="muted">{t('sidebar.empty')}</p>}
    <ul className={styles.rows}>
      {items.map(({ directory }, index) => {
        const preset = presetOf(directory.cron)
        return <li key={directory.id} className={styles.row}>
          <div className={styles.rowText}>
            <strong>{directory.name}</strong>
            <span className="mono small muted">{directory.path}</span>
            <span className="small muted">
              {preset === 'custom' ? directory.cron : t(`schedule.presets.${preset}`)}
              {' · '}{t(`engines.${directory.engine}.name`)}
              {!directory.enabled && <> · {t('settings.paused')}</>}
            </span>
          </div>
          <div className={styles.rowActions}>
            <button type="button" className="icon-button" disabled={index === 0 || order.isPending}
              aria-label={t('settings.moveUp', { name: directory.name })} onClick={() => move(index, -1)}>
              <Icon name="up" size={18} />
            </button>
            <button type="button" className="icon-button" disabled={index === items.length - 1 || order.isPending}
              aria-label={t('settings.moveDown', { name: directory.name })} onClick={() => move(index, 1)}>
              <Icon name="down" size={18} />
            </button>
            <button type="button" className="button secondary small" onClick={() => onNavigate(directory.id)}
              aria-label={t('settings.editName', { name: directory.name })}>
              <Icon name="edit" size={16} />{t('common.edit')}
            </button>
          </div>
        </li>
      })}
    </ul>
  </div>
}

/** ⚙ Settings: the directories and their scan options, and the general settings. */
export function SettingsDialog({ target, onNavigate, onClose }: {
  target: SettingsTarget
  onNavigate: (target: SettingsTarget) => void
  onClose: () => void
}) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const tab = target === 'general' ? 'general' : 'directories'
  return <ModalOverlay isOpen isDismissable onOpenChange={open => { if (!open) onClose() }} className={styles.overlay}>
    <Modal className={styles.modal}>
      <Dialog className={styles.dialog} aria-labelledby="settings-title">
        <div className={styles.titleBar}>
          <Heading slot="title" id="settings-title">{t('settings.title')}</Heading>
          <button type="button" className="icon-button" aria-label={t('common.close')} onClick={onClose}>
            <Icon name="close" />
          </button>
        </div>
        <Tabs selectedKey={tab} onSelectionChange={key => onNavigate(key === 'general' ? 'general' : 'directories')}>
          <TabList aria-label={t('settings.title')} className={styles.tabs}>
            <Tab id="directories" className={styles.tab}>{t('settings.tabs.directories')}</Tab>
            <Tab id="general" className={styles.tab}>{t('settings.tabs.general')}</Tab>
          </TabList>
          <TabPanel id="directories" className={styles.panel}>
            {target === 'new' || typeof target === 'number'
              ? <DirectoryForm key={String(target)} id={target === 'new' ? null : target}
                onDone={saved => {
                  if (saved !== null && target === 'new') {
                    onClose()
                    navigate(`/directories/${saved}`)
                  } else onNavigate('directories')
                }}
                onDeleted={() => {
                  onNavigate('directories')
                  navigate('/')
                }} />
              : <DirectoryList onNavigate={onNavigate} />}
          </TabPanel>
          <TabPanel id="general" className={styles.panel}><GeneralSettings /></TabPanel>
        </Tabs>
      </Dialog>
    </Modal>
  </ModalOverlay>
}
