import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { DemoStore } from '../demo/store'
import { I18nRoot } from '../i18n'
import { formatBytes, formatDateTime } from '../lib/format'
import { DirectoryPage } from './DirectoryPage'
import { LineChart, type Point } from './LineChart'
import { NOW } from '../test/clock'
import { renderWithProviders, resetApi, server } from '../test/render'

let demo: DemoStore

beforeAll(() => server.listen({ onUnhandledFrame: 'error' }))
beforeEach(() => { demo = resetApi() })
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const media = (search = '') => renderWithProviders(<DirectoryPage />, { route: `/directories/1${search}`, path: '/directories/:id' })

/** Text as the page shows it once whitespace is normalized (sizes have a non-breaking space). */
const plain = (text: string) => text.replace(/\s+/g, ' ')

/** The files the page downloads (jsdom saves nothing): their names and contents. */
function downloads() {
  const blobs = new Map<string, Blob>()
  const files: { name: string; blob: Blob | undefined }[] = []
  Object.assign(URL, {
    createObjectURL: (blob: Blob) => {
      const url = `blob:test/${blobs.size}`
      blobs.set(url, blob)
      return url
    },
    revokeObjectURL: () => {},
  })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    files.push({ name: this.download, blob: blobs.get(this.href) })
  })
  return files
}

describe('scan logs', () => {
  afterEach(() => vi.restoreAllMocks())

  test('each scan that started downloads its log from the latest scans; a queued one has none yet', async () => {
    const user = userEvent.setup()
    const files = downloads()
    // Photos is scanning, so a scan of Media waits in the queue.
    demo.enqueue(2)
    demo.enqueue(1)
    media()
    const scans = await screen.findByRole('region', { name: 'Latest scans' })
    expect(within(scans).getByRole('columnheader', { name: 'Log' })).toBeInTheDocument()
    const [, queued, latest] = within(scans).getAllByRole('row')
    expect(queued).toHaveTextContent('Queued')
    expect(within(queued).queryByRole('button', { name: /^Download the log/ })).not.toBeInTheDocument()

    const scan = demo.scanList(1, null, 10).find(item => item.status === 'completed')
    if (!scan) throw new Error('no completed scan of Media')
    const button = within(latest).getByRole('button', { name: `Download the log of the scan of ${formatDateTime(scan.finished_at)}` })
    expect(button).toHaveAttribute('title', 'Download the log')
    await user.click(button)
    await waitFor(() => expect(files).toHaveLength(1))
    const log = demo.scanLog(scan.id)
    expect(files[0].name).toBe(log.name)
    expect(files[0].name).toMatch(/^Media_\d{4}-\d\d-\d\d_\d{4}\.log$/)
    expect(await files[0].blob?.text()).toBe(log.text)
  })

  test('a log that expired meanwhile says so, and its button goes', async () => {
    const user = userEvent.setup()
    const files = downloads()
    media()
    const scans = await screen.findByRole('region', { name: 'Latest scans' })
    const buttons = await within(scans).findAllByRole('button', { name: /^Download the log of the scan of / })
    const count = buttons.length
    demo.settings.scan_log_days = 1
    await user.click(buttons[count - 1])
    expect(await screen.findByRole('alert')).toHaveTextContent('The log of this scan no longer exists: logs are deleted after the days set in Settings → General.')
    await waitFor(() => expect(within(scans).getAllByRole('button', { name: /^Download the log of the scan of / }).length).toBeLessThan(count))
    expect(files).toEqual([])
  })

  test('the errors of the last scan download with the rest of its log', async () => {
    const user = userEvent.setup()
    const files = downloads()
    renderWithProviders(<DirectoryPage />, { route: '/directories/3', path: '/directories/:id' })
    const last = demo.summary(3).last_scan
    if (!last) throw new Error('no scan of Backups')
    await user.click(await screen.findByText(/^Completed with \d+ errors?$/))
    await user.click(screen.getByRole('button', { name: 'Download the log, with every error' }))
    await waitFor(() => expect(files).toHaveLength(1))
    expect(files[0].name).toMatch(/^Backups_\d{4}-\d\d-\d\d_\d{4}\.log$/)
    const text = await files[0].blob?.text()
    for (const error of last.error_sample) expect(text).toContain(`  Error: /data/backups/${error.path}: ${error.message}\n`)
  })
})

describe('earlier scans in the treemap', () => {
  test('a date in the latest scans shows the folders of that scan, until going back to the latest one', async () => {
    const user = userEvent.setup()
    const [latest, previous] = demo.scanList(1, null, 10).filter(scan => scan.has_detail)
    const movies = (scan: number) => demo.tree(1, scan, '').children.find(child => child.name === 'Movies')?.size ?? 0
    expect(movies(previous.id)).not.toBe(movies(latest.id))
    media()
    const scans = await screen.findByRole('region', { name: 'Latest scans' })
    const dates = await within(scans).findAllByRole('button', { name: /\(see its folders\)$/ })
    expect(dates[0]).toHaveAccessibleName(`${formatDateTime(latest.finished_at)} (see its folders)`)
    const folders = await screen.findByRole('region', { name: 'Folders in Media' })
    await waitFor(() => expect(within(folders).getByRole('row', { name: /^Movies/ })).toHaveTextContent(plain(formatBytes(movies(latest.id)))))

    await user.click(dates[1])
    expect(await screen.findByText(`Showing an earlier scan, from ${formatDateTime(previous.finished_at)}.`)).toBeInTheDocument()
    await waitFor(() => expect(within(folders).getByRole('row', { name: /^Movies/ })).toHaveTextContent(plain(formatBytes(movies(previous.id)))))
    expect(screen.getByLabelText('Scan')).toHaveValue(String(previous.id))
    expect(dates[1]).toHaveAttribute('aria-current', 'true')
    // The treemap is above the history: the keyboard goes there too.
    expect(screen.getByRole('region', { name: 'Contents' })).toHaveFocus()

    await user.click(screen.getByRole('button', { name: 'Back to the latest scan' }))
    await waitFor(() => expect(within(folders).getByRole('row', { name: /^Movies/ })).toHaveTextContent(plain(formatBytes(movies(latest.id)))))
    expect(screen.queryByText(/^Showing an earlier scan/)).not.toBeInTheDocument()
    expect(screen.getByLabelText('Scan')).toHaveValue('')
    expect(dates[1]).not.toHaveAttribute('aria-current')
    // The button went away with its notice, but not the keyboard.
    expect(screen.getByRole('region', { name: 'Contents' })).toHaveFocus()
  })

  test('the oldest kept scan has no changes to show, and only says so once it loses its folders', async () => {
    const user = userEvent.setup()
    const kept = demo.scanList(1, null, 2000).filter(scan => scan.has_detail)
    const oldest = kept[kept.length - 1]
    const { client } = media()
    const folders = await screen.findByRole('region', { name: 'Folders in Media' })
    expect(await within(folders).findByRole('columnheader', { name: 'Change' })).toBeInTheDocument()

    // Nothing earlier keeps its folders: no changes, not even those of the scan shown before.
    await user.selectOptions(screen.getByLabelText('Scan'), String(oldest.id))
    expect(await screen.findByText(`Showing an earlier scan, from ${formatDateTime(oldest.finished_at)}.`)).toBeInTheDocument()
    expect(await screen.findByText('There is no earlier scan to compare with.')).toBeInTheDocument()
    await waitFor(() => expect(within(folders).queryByRole('columnheader', { name: 'Change' })).not.toBeInTheDocument())

    // Fewer scans keep their folders, and the page refreshes: only the notice and its way back are left.
    demo.settings.keep_scans = 2
    await client.invalidateQueries()
    expect(await screen.findByText('This scan no longer keeps its folders.')).toBeInTheDocument()
    expect(screen.queryByText(/^Showing an earlier scan/)).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Folders in Media' })).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Back to the latest scan' })).toHaveLength(1)
  })

  test('a scan that no longer keeps its folders says so, with the way back to the latest one', async () => {
    const user = userEvent.setup()
    const old = demo.scanList(1, null, 2000).find(scan => scan.status === 'completed' && !scan.has_detail)
    media(`?scan=${old?.id}`)
    expect(await screen.findByText('This scan no longer keeps its folders.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Back to the latest scan' }))
    expect(await screen.findByRole('region', { name: 'Folders in Media' })).toBeInTheDocument()
    expect(screen.queryByText('This scan no longer keeps its folders.')).not.toBeInTheDocument()
  })

  test('the size chart opens the scan of the point nearest to the pointer, if it keeps its folders', () => {
    const opened: (number | undefined)[] = []
    const points: Point[] = [0, 1, 2].map(day => ({
      time: NOW + day * 86_400_000, value: 100 + day, label: `day ${day}`, id: day + 1, openable: day > 0,
    }))
    render(<I18nRoot><LineChart points={points} label="Size" format={String} onOpen={point => opened.push(point.id)} /></I18nRoot>)
    const chart = screen.getByRole('img', { name: /^Size:/ })
    // jsdom lays nothing out: the chart is 200 px wide and its points are at x = 64, 126 and 188.
    fireEvent.pointerMove(chart, { clientX: 80 })
    expect(screen.getByText('day 0')).toBeInTheDocument()
    expect(screen.getByText('Its folders are no longer kept')).toBeInTheDocument()
    fireEvent.click(chart, { clientX: 80 })
    expect(opened).toEqual([])
    fireEvent.pointerMove(chart, { clientX: 170 })
    expect(screen.getByText('day 2')).toBeInTheDocument()
    expect(screen.getByText('Click to see its folders')).toBeInTheDocument()
    fireEvent.click(chart, { clientX: 170 })
    expect(opened).toEqual([3])
    fireEvent.pointerLeave(chart)
    expect(screen.queryByText('day 2')).not.toBeInTheDocument()
  })
})
