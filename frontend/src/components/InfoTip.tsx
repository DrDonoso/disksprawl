import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { Icon } from './Icon'
import styles from './InfoTip.module.css'

const MARGIN = 8

/**
 * An ⓘ button with an explanation: it opens on hover and on keyboard focus, and on a tap or click
 * it stays open until the next tap, Escape or a tap outside. Screen readers read the text as the
 * button's description.
 */
export function InfoTip({ label, children }: { label: string; children: ReactNode }) {
  const [hovered, setHovered] = useState(false)
  const [focused, setFocused] = useState(false)
  const [pinned, setPinned] = useState(false)
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null)
  const id = useId()
  const wrapper = useRef<HTMLSpanElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const bubble = useRef<HTMLSpanElement>(null)
  const open = hovered || focused || pinned

  useEffect(() => {
    if (!open) return
    const close = () => {
      setPinned(false)
      setHovered(false)
    }
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !wrapper.current?.contains(event.target)) close()
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.stopPropagation()
      close()
      setFocused(false)
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape, true)
    window.addEventListener('scroll', close, true)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', escape, true)
      window.removeEventListener('scroll', close, true)
    }
  }, [open])

  // Below the button, kept inside the viewport (a fixed bubble also escapes scrolling containers).
  useLayoutEffect(() => {
    if (!open || !button.current || !bubble.current) {
      setPosition(null)
      return
    }
    const anchor = button.current.getBoundingClientRect()
    const width = bubble.current.offsetWidth
    const height = bubble.current.offsetHeight
    const left = Math.min(Math.max(MARGIN, anchor.left + anchor.width / 2 - width / 2), window.innerWidth - width - MARGIN)
    const below = anchor.bottom + 6
    const top = below + height > window.innerHeight - MARGIN ? Math.max(MARGIN, anchor.top - height - 6) : below
    setPosition({ top, left })
  }, [open])

  return <span ref={wrapper} className={styles.wrapper}
    onPointerEnter={event => { if (event.pointerType === 'mouse') setHovered(true) }}
    onPointerLeave={event => { if (event.pointerType === 'mouse') setHovered(false) }}>
    <button ref={button} type="button" className={styles.button} aria-label={label} aria-describedby={id}
      aria-expanded={open}
      onClick={() => setPinned(previous => !previous)}
      onFocus={event => { if (event.currentTarget.matches(':focus-visible')) setFocused(true) }}
      onBlur={() => { setFocused(false); setPinned(false) }}>
      <Icon name="info" size={16} />
    </button>
    <span ref={bubble} role="tooltip" id={id} className={styles.bubble} hidden={!open}
      style={position ? { top: position.top, left: position.left } : { visibility: 'hidden' }}>
      {children}
    </span>
  </span>
}
