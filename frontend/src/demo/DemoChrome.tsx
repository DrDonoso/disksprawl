import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Dialog, Heading, Modal, ModalOverlay } from 'react-aria-components'
import { useI18n } from '../i18n'
import { DEMO_REPOSITORY_URL } from './config'
import styles from './demo.module.css'

let welcomed = false

/** Shows the welcome dialog again, as a reload does (tests). */
export function resetDemoWelcome(): void {
  welcomed = false
}

/** Once per page load: what the demo is and that its data is synthetic. */
function Welcome() {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  // Decided after the first commit: React may render and discard a component more than once.
  useEffect(() => {
    if (welcomed) return
    welcomed = true
    setOpen(true)
  }, [])
  return <ModalOverlay isOpen={open} isDismissable onOpenChange={setOpen} className={styles.overlay}>
    <Modal className={styles.modal}>
      <Dialog className={styles.dialog}>
        <Heading slot="title">{t('demo.welcome.title')}</Heading>
        <p>{t('demo.welcome.intro')}</p>
        <p>{t('demo.welcome.data')}</p>
        <button type="button" className="button" autoFocus onClick={() => setOpen(false)}>{t('demo.welcome.explore')}</button>
      </Dialog>
    </Modal>
  </ModalOverlay>
}

/** A slim bar at the bottom of every page; its height is published as --demo-banner-height. */
function Banner() {
  const { t } = useI18n()
  const banner = useRef<HTMLElement>(null)
  useLayoutEffect(() => {
    const element = banner.current
    if (!element) return
    const root = document.documentElement
    const publish = () => root.style.setProperty('--demo-banner-height', `${element.offsetHeight}px`)
    publish()
    const observer = new ResizeObserver(publish)
    observer.observe(element)
    return () => {
      observer.disconnect()
      root.style.removeProperty('--demo-banner-height')
    }
  }, [])
  return <aside ref={banner} className={styles.banner} aria-label={t('demo.banner.label')}>
    <p>{t('demo.banner.text')}</p>
    <a href={DEMO_REPOSITORY_URL} target="_blank" rel="noopener noreferrer" aria-label={t('demo.banner.sourceLabel')}>
      {t('demo.banner.source')}
    </a>
  </aside>
}

/** Everything the public demo adds to the app: the banner and the welcome dialog. */
export default function DemoChrome() {
  useEffect(() => {
    document.documentElement.dataset.demo = 'true'
    return () => { delete document.documentElement.dataset.demo }
  }, [])
  return <>
    <Banner />
    <Welcome />
  </>
}
