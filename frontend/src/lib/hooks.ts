import { useCallback, useRef, useState, useSyncExternalStore } from 'react'

let tick = 0
const tickers = new Set<() => void>()
let timer: ReturnType<typeof setInterval> | undefined

function subscribeTick(listener: () => void): () => void {
  tickers.add(listener)
  timer ??= setInterval(() => {
    tick += 1
    for (const ticker of tickers) ticker()
  }, 15_000)
  return () => {
    tickers.delete(listener)
    if (!tickers.size && timer !== undefined) {
      clearInterval(timer)
      timer = undefined
    }
  }
}

/** The current time, for texts like "3 hours ago": the component also re-renders every 15 s. */
export function useNow(): number {
  useSyncExternalStore(subscribeTick, () => tick, () => 0)
  return Date.now()
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
