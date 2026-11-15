import { useCallback, useRef, useState, useSyncExternalStore } from 'react'
import type { Theme } from './colors'

const QUERY = '(prefers-color-scheme: dark)'

function subscribe(listener: () => void): () => void {
  if (typeof window === 'undefined' || !window.matchMedia) return () => {}
  const media = window.matchMedia(QUERY)
  media.addEventListener('change', listener)
  return () => media.removeEventListener('change', listener)
}

function current(): Theme {
  return typeof window !== 'undefined' && window.matchMedia?.(QUERY).matches ? 'dark' : 'light'
}

/** The system's light or dark theme, followed live (the CSS uses the same media query). */
export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, current, () => 'light')
}

/** The current time, refreshed every `interval` ms, for texts like "3 hours ago". */
export function useNow(interval = 30_000): number {
  return useSyncExternalStore(
    listener => {
      const timer = setInterval(listener, interval)
      return () => clearInterval(timer)
    },
    () => Math.floor(Date.now() / interval) * interval,
    () => 0,
  )
}

/** The width and height of an element, followed with a ResizeObserver. */
export function useElementSize<T extends HTMLElement>(): [(element: T | null) => void, { width: number; height: number }] {
  const [size, setSize] = useState({ width: 0, height: 0 })
  const observer = useRef<ResizeObserver | null>(null)
  const ref = useCallback((element: T | null) => {
    observer.current?.disconnect()
    if (!element) return
    const measure = () => setSize(previous => {
      const width = Math.floor(element.clientWidth)
      const height = Math.floor(element.clientHeight)
      return previous.width === width && previous.height === height ? previous : { width, height }
    })
    measure()
    observer.current = new ResizeObserver(measure)
    observer.current.observe(element)
  }, [])
  return [ref, size]
}
