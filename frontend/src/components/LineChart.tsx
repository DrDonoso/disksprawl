import { useI18n } from '../i18n'
import { formatDate } from '../lib/format'
import { useElementSize } from '../lib/hooks'
import styles from './History.module.css'

export interface Point {
  time: number
  value: number
  label: string
}

const HEIGHT = 170
const PAD = { top: 10, right: 12, bottom: 24, left: 64 }

/** A small line chart in SVG: the value over time, with optional horizontal threshold lines. */
export function LineChart({ points, label, format, min, max, thresholds = [], color = 'var(--chart-line)' }: {
  points: Point[]
  label: string
  format: (value: number) => string
  min?: number
  max?: number
  thresholds?: { value: number; color: string }[]
  color?: string
}) {
  const { t } = useI18n()
  const [ref, size] = useElementSize<HTMLDivElement>()
  const width = Math.max(size.width, 200)
  if (points.length === 0) return <div ref={ref} className={styles.chart}><p className="muted">{t('history.empty')}</p></div>
  const times = points.map(point => point.time)
  const values = points.map(point => point.value)
  let low = min ?? Math.min(...values)
  let high = max ?? Math.max(...values)
  if (high === low) {
    low = min ?? low * 0.95
    high = max ?? (high * 1.05 || 1)
  }
  const start = Math.min(...times)
  const end = Math.max(...times)
  const x = (time: number) => PAD.left + (end === start ? (width - PAD.left - PAD.right) / 2 : ((time - start) / (end - start)) * (width - PAD.left - PAD.right))
  const y = (value: number) => PAD.top + (1 - (value - low) / (high - low)) * (HEIGHT - PAD.top - PAD.bottom)
  const line = points.map(point => `${x(point.time).toFixed(1)},${y(point.value).toFixed(1)}`).join(' ')
  const area = `${x(points[0].time).toFixed(1)},${y(low)} ${line} ${x(points[points.length - 1].time).toFixed(1)},${y(low)}`
  const ticks = [low, (low + high) / 2, high]
  const first = points[0]
  const last = points[points.length - 1]
  const description = t('history.chartSummary', {
    label, first: format(first.value), firstDate: formatDate(new Date(first.time).toISOString()),
    last: format(last.value), lastDate: formatDate(new Date(last.time).toISOString()),
  })
  return <div ref={ref} className={styles.chart}>
    <svg width={width} height={HEIGHT} role="img" aria-label={description}>
      {ticks.map((tick, index) => <g key={index}>
        <line x1={PAD.left} x2={width - PAD.right} y1={y(tick)} y2={y(tick)} className={styles.grid} />
        <text x={PAD.left - 6} y={y(tick) + 4} textAnchor="end" className={styles.axis}>{format(tick)}</text>
      </g>)}
      {thresholds.filter(threshold => threshold.value > low && threshold.value < high).map(threshold =>
        <line key={threshold.value} x1={PAD.left} x2={width - PAD.right} y1={y(threshold.value)} y2={y(threshold.value)}
          stroke={threshold.color} strokeDasharray="4 4" />)}
      <polygon points={area} fill="var(--chart-area)" />
      <polyline points={line} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" />
      {points.length <= 120 && points.map((point, index) => <circle key={index} cx={x(point.time)} cy={y(point.value)} r={3} fill={color}>
        <title>{point.label}</title>
      </circle>)}
      <text x={PAD.left} y={HEIGHT - 6} className={styles.axis}>{formatDate(new Date(start).toISOString())}</text>
      <text x={width - PAD.right} y={HEIGHT - 6} textAnchor="end" className={styles.axis}>{formatDate(new Date(end).toISOString())}</text>
    </svg>
  </div>
}
