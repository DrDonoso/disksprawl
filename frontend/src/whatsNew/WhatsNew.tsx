import { useEffect, useRef, useState } from 'react'
import { Dialog, Heading, Modal, ModalOverlay } from 'react-aria-components'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useWhatsNew } from '../api/hooks'
import type { WhatsNewOut } from '../api/schema.gen'
import { Icon, type IconName } from '../components/Icon'
import { useI18n } from '../i18n'
import { NOTES, byKind, byMonth, type Note, type NoteKind } from './notes'
import styles from './WhatsNew.module.css'

const NEWS_SHOWN = 6
const BATCH = 500
const KIND_ICONS: Record<NoteKind, IconName> = { new: 'sparkles', improved: 'trending', fixed: 'tool' }

/** Marks notes as read: at once in the cache, then in the API (a failure only means they show again later). */
async function markRead(queryClient: QueryClient, ids: readonly string[]): Promise<void> {
  if (ids.length === 0) return
  queryClient.setQueryData<WhatsNewOut>(['whats-new'], previous => ({ seen: [...new Set([...(previous?.seen ?? []), ...ids])].toSorted() }))
  for (let start = 0; start < ids.length; start += BATCH) {
    try {
      await api('/whats-new/seen', 'POST', { ids: ids.slice(start, start + BATCH) })
    } catch {
      return
    }
  }
}

function NoteCard({ note, heading: Level, titleRef }: { note: Note; heading: 'h4' | 'h5'; titleRef?: (element: HTMLElement | null) => void }) {
  const { language, t } = useI18n()
  const text = note[language]
  return <li className={`${styles.note} ${styles[note.kind]}`}>
    <span className={styles.icon}><Icon name={KIND_ICONS[note.kind]} size={18} /></span>
    <div className={styles.text}>
      <span className={styles.kind}>{t(`whatsNew.kinds.${note.kind}`)}</span>
      <Level ref={titleRef} tabIndex={-1} className={styles.title}>{text.title}</Level>
      <p className={styles.body}>{text.body}</p>
    </div>
  </li>
}

function Window({ title, intro, onClose, children, footer }: { title: string; intro: string; onClose: () => void; children: React.ReactNode; footer?: React.ReactNode }) {
  const { t } = useI18n()
  return <ModalOverlay isOpen isDismissable className={styles.overlay} onOpenChange={open => { if (!open) onClose() }}>
    <Modal className={styles.modal}>
      <Dialog className={styles.dialog} aria-label={title}>
        <div className={styles.titleBar}>
          <Heading slot="title" level={2}>{title}</Heading>
          <button type="button" className="icon-button" aria-label={t('common.close')} onClick={onClose}><Icon name="close" /></button>
        </div>
        <p className="muted">{intro}</p>
        {children}
        {footer}
      </Dialog>
    </Modal>
  </ModalOverlay>
}

function NewsDialog({ notes, onClose }: { notes: Note[]; onClose: () => void }) {
  const { t } = useI18n()
  const [shown, setShown] = useState(NEWS_SHOWN)
  const firstRevealed = useRef<HTMLElement | null>(null)
  const revealedFrom = useRef(-1)
  useEffect(() => {
    if (revealedFrom.current >= 0) firstRevealed.current?.focus()
  }, [shown])
  const hidden = notes.length - shown
  return <Window title={t('whatsNew.title')} intro={t('whatsNew.intro')} onClose={onClose}
    footer={<div className={styles.footer}>
      {hidden > 0 && <button type="button" className="button secondary" onClick={() => { revealedFrom.current = shown; setShown(shown + NEWS_SHOWN) }}>
        {t('whatsNew.showMore', { count: Math.min(hidden, NEWS_SHOWN) })}
      </button>}
      <button type="button" className="button" onClick={onClose}>{t('whatsNew.gotIt')}</button>
    </div>}>
    <ul className={styles.notes}>
      {notes.slice(0, shown).map((note, index) => <NoteCard key={note.id} note={note} heading="h4"
        titleRef={index === revealedFrom.current ? element => { firstRevealed.current = element } : undefined} />)}
    </ul>
  </Window>
}

function AllNotesDialog({ onClose }: { onClose: () => void }) {
  const { locale, t } = useI18n()
  const month = new Intl.DateTimeFormat(locale, { month: 'long', year: 'numeric', timeZone: 'UTC' })
  return <Window title={t('whatsNew.title')} intro={t('whatsNew.allIntro')} onClose={onClose}>
    {NOTES.length === 0 && <p className="muted">{t('whatsNew.empty')}</p>}
    {byMonth(NOTES).map(group => <section key={group.month} className={styles.month}>
      <h4 className={styles.monthTitle}>{month.format(new Date(`${group.month}T00:00:00Z`))}</h4>
      <ul className={styles.notes}>{group.notes.map(note => <NoteCard key={note.id} note={note} heading="h5" />)}</ul>
    </section>)}
  </Window>
}

/** What is new since the last visit (once, when the app opens) and the button that lists every note. */
export function WhatsNew() {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const answer = useWhatsNew()
  const [news, setNews] = useState<Note[] | null>(null)
  const [all, setAll] = useState(false)
  const decided = useRef(false)
  const button = useRef<HTMLButtonElement>(null)

  // Decided from the first answer only: nothing recorded (a new install) has nothing to catch up on.
  useEffect(() => {
    if (decided.current || !answer.data) return
    decided.current = true
    const seen = answer.data.seen
    const unread = NOTES.filter(note => !seen?.includes(note.id))
    if (seen === null || unread.length === 0) void markRead(queryClient, unread.map(note => note.id))
    else setNews(byKind(unread))
  }, [answer.data])

  function closeNews() {
    if (news) void markRead(queryClient, news.map(note => note.id))
    setNews(null)
  }

  function closeAll() {
    void markRead(queryClient, NOTES.map(note => note.id))
    setAll(false)
    requestAnimationFrame(() => button.current?.focus())
  }

  return <>
    <button ref={button} type="button" className="icon-button" aria-label={t('whatsNew.title')} title={t('whatsNew.title')} onClick={() => setAll(true)}>
      <Icon name="bell" />
    </button>
    {news && !all && <NewsDialog notes={news} onClose={closeNews} />}
    {all && <AllNotesDialog onClose={closeAll} />}
  </>
}
