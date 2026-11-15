import { describe, expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { DirectorySummaryOut, ProgressOut, ScanOut } from '../api/schema.gen'
import { I18nRoot } from '../i18n'
import { Freshness } from './DirectoryPage'
import { InfoTip } from './InfoTip'
import { ScanProgress } from './ScanProgress'
import { renderWithProviders } from '../test/render'

const NOW = Date.UTC(2026, 10, 15, 12)
const hoursAgo = (hours: number) => new Date(NOW - hours * 3600_000).toISOString()

const SCAN: ScanOut = {
  id: 1, directory_id: 1, status: 'completed', trigger: 'scheduled', engine: 'native',
  options: { engine: 'native', threads: 2, pace: 'balanced', low_priority: true, measure: 'apparent', one_filesystem: true, excludes: [] },
  queued_at: hoursAgo(3.1), started_at: hoursAgo(3.05), finished_at: hoursAgo(3), duration_seconds: 2520, total_bytes: 10,
  file_count: 1, dir_count: 0, error_count: 0, error_sample: [], message: null, capacity_total: null, capacity_used: null,
  capacity_free: null, has_detail: true,
}

function summary(changes: Partial<DirectorySummaryOut>): DirectorySummaryOut {
  return {
    directory: {} as DirectorySummaryOut['directory'], last_scan: SCAN, last_attempt: null, progress: null,
    capacity: null, next_run: null, ...changes,
  }
}

describe('"Updated …" in the header', () => {
  test('when the last scan completed: how long ago, with the date, duration and engine on hover', () => {
    render(<I18nRoot><Freshness summary={summary({})} now={NOW} /></I18nRoot>)
    const time = screen.getByText('Updated 3 hours ago')
    expect(time).toHaveAttribute('title', expect.stringMatching(/took 42 min · Native$/))
  })

  test('when it was never scanned', () => {
    render(<I18nRoot><Freshness summary={summary({ last_scan: null })} now={NOW} /></I18nRoot>)
    expect(screen.getByText('Not scanned yet')).toBeInTheDocument()
  })

  test('when the last attempt failed: since when, and which data is shown', () => {
    const failed = { ...SCAN, id: 2, status: 'failed' as const, finished_at: hoursAgo(1), message: 'path_not_found' }
    render(<I18nRoot><Freshness summary={summary({ last_attempt: failed })} now={NOW} /></I18nRoot>)
    expect(screen.getByRole('status')).toHaveTextContent(
      /^The last scan failed 1 hour ago; showing the data from 15\/11, 09:00\. Reason: This folder does not exist \(it may not be mounted\)\.$/,
    )
  })

  test('when it failed before any data existed', () => {
    const cancelled = { ...SCAN, id: 2, status: 'cancelled' as const, finished_at: hoursAgo(2) }
    render(<I18nRoot><Freshness summary={summary({ last_scan: null, last_attempt: cancelled })} now={NOW} /></I18nRoot>)
    expect(screen.getByRole('status')).toHaveTextContent('The last scan was cancelled 2 hours ago.')
  })
})

const PROGRESS: ProgressOut = {
  scan_id: 5, directory_id: 1, status: 'running', phase: 'scanning', position: null, queued_at: hoursAgo(0.1),
  started_at: hoursAgo(0.1), elapsed_seconds: 360, counters: true, dirs: 1234, files: 56789, bytes: 2 ** 40,
  current: 'Media/Movies', percent: 0.45, remaining_seconds: 440, previous_duration_seconds: 2520, engine: 'native',
  pace: 'balanced', threads: 2,
}

describe('the progress panel', () => {
  test('with an estimate: percentage, time left, counters and the folder being read', () => {
    renderWithProviders(<ScanProgress progress={PROGRESS} />)
    expect(screen.getByRole('heading', { name: 'Scanning…' })).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '45')
    expect(screen.getByText('45% done · ≈ 7 min left')).toBeInTheDocument()
    expect(screen.getByText('1,234')).toBeInTheDocument()
    expect(screen.getByText('56,789')).toBeInTheDocument()
    expect(screen.getByText('Media/Movies')).toBeInTheDocument()
    expect(screen.getByText('Native · Balanced · 2 threads')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancel scan' })).toBeEnabled()
  })

  test('without an estimate: an indeterminate bar, the time elapsed and how long the last scan took', () => {
    renderWithProviders(<ScanProgress progress={{ ...PROGRESS, engine: 'gdu', counters: false, percent: null, remaining_seconds: null, current: null }} />)
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow')
    expect(screen.getByText(/Elapsed 6 min · the last scan took 42 min/)).toBeInTheDocument()
    expect(screen.getByText('gdu reports nothing until it finishes.')).toBeInTheDocument()
  })

  test('queued and saving phases', () => {
    const { unmount } = renderWithProviders(<ScanProgress progress={{ ...PROGRESS, status: 'queued', phase: 'queued', position: 2 }} />)
    expect(screen.getByRole('heading', { name: 'Queued: 2nd in line' })).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    unmount()
    renderWithProviders(<ScanProgress progress={{ ...PROGRESS, phase: 'saving' }} />)
    expect(screen.getByRole('heading', { name: 'Saving results…' })).toBeInTheDocument()
  })
})

describe('info tooltips', () => {
  test('open on hover, on keyboard focus and on tap, and describe their button', async () => {
    const user = userEvent.setup()
    render(<I18nRoot><InfoTip label="About threads">How many folders are read at once.</InfoTip><button>after</button></I18nRoot>)
    const button = screen.getByRole('button', { name: 'About threads' })
    expect(button).toHaveAccessibleDescription('How many folders are read at once.')
    expect(screen.getByRole('tooltip', { hidden: true })).not.toBeVisible()
    await user.hover(button)
    expect(screen.getByRole('tooltip')).toBeVisible()
    await user.unhover(button)
    expect(screen.getByRole('tooltip', { hidden: true })).not.toBeVisible()
    await user.click(button)
    await user.unhover(button)
    expect(screen.getByRole('tooltip')).toBeVisible()
    await user.keyboard('{Escape}')
    expect(screen.getByRole('tooltip', { hidden: true })).not.toBeVisible()
  })
})
