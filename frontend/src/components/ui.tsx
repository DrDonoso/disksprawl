import type { ReactNode } from 'react'
import { ProgressBar } from 'react-aria-components'
import styles from './ui.module.css'

export function Notice({ children, error = false }: { children: ReactNode; error?: boolean }) {
  return <div className={`notice${error ? ' error' : ''}`} role={error ? 'alert' : 'status'}>{children}</div>
}

/** Volume level: ok, warning (from the warning threshold) or critical. */
export type Level = 'ok' | 'warn' | 'critical'

export function levelOf(percent: number, warn: number, critical: number): Level {
  return percent >= critical ? 'critical' : percent >= warn ? 'warn' : 'ok'
}

/** A horizontal bar: determinate (0–1) or indeterminate (null), with an accessible label. */
export function Bar({ value, label, valueText, level = 'ok', size = 'normal' }: {
  value: number | null
  label: string
  valueText?: string
  level?: Level | 'progress'
  size?: 'normal' | 'thin'
}) {
  const determinate = value !== null
  return <ProgressBar aria-label={label} isIndeterminate={!determinate} value={determinate ? Math.round(value * 1000) / 10 : undefined}
    valueLabel={valueText} className={`${styles.bar} ${styles[size]}`}>
    <div className={styles.track}>
      <div className={`${styles.fill} ${styles[level]} ${determinate ? '' : styles.indeterminate}`}
        style={determinate ? { width: `${Math.min(100, Math.max(0, value * 100))}%` } : undefined} />
    </div>
  </ProgressBar>
}
