import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, test } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { DirectoryForm } from './DirectoryForm'
import { Shell } from './Shell'
import { renderWithProviders, resetApi, server } from '../test/render'

beforeAll(() => server.listen({ onUnhandledFrame: 'error' }))
beforeEach(() => { resetApi() })
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

describe('the directory form', () => {
  test('new directories start with the defaults, and every option explains itself', async () => {
    renderWithProviders(<DirectoryForm id={null} onDone={() => {}} onDeleted={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Add a directory' })).toBeInTheDocument()
    expect(screen.getByLabelText('Schedule')).toHaveValue('daily')
    expect(await screen.findByRole('radio', { name: /Native/ })).toBeChecked()
    expect(screen.getByLabelText('Threads')).toHaveValue(2)
    expect(screen.getByRole('radio', { name: 'Balanced' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'Apparent size' })).toBeChecked()
    expect(screen.getByRole('switch', { name: 'Low priority' })).toBeChecked()
    expect(screen.getByRole('switch', { name: 'Stay on one file system' })).toBeChecked()
    expect(within(screen.getByRole('list', { name: 'Excluded patterns' })).getAllByRole('listitem').map(item => item.textContent))
      .toEqual(['#snapshot', '.snapshot', '.zfs'])
    for (const name of ['Native', 'du', 'ncdu', 'gdu', 'Threads', 'Maximum', 'Balanced', 'Gentle', 'Low priority', 'Apparent size',
      'Space on disk', 'Stay on one file system', 'Exclusions', 'Schedule']) {
      expect(screen.getByRole('button', { name: `About ${name}` })).toHaveAccessibleDescription(/\w/)
    }
    expect(screen.getByRole('button', { name: 'About gdu' })).toHaveAccessibleDescription(/8 threads at most/)
  })

  test('threads follow the engine: one for du, eight at most for gdu', async () => {
    const user = userEvent.setup()
    renderWithProviders(<DirectoryForm id={null} onDone={() => {}} onDeleted={() => {}} />)
    await user.click(await screen.findByRole('radio', { name: /^du/ }))
    expect(screen.getByLabelText('Threads')).toBeDisabled()
    expect(screen.getByLabelText('Threads')).toHaveValue(1)
    expect(screen.getByText('du always reads with one thread.')).toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: /^gdu/ }))
    expect(screen.getByLabelText('Threads')).toBeEnabled()
    expect(screen.getByLabelText('Threads')).toHaveAttribute('max', '8')
  })

  test('the schedule shows the next runs, and a custom cron is validated', async () => {
    const user = userEvent.setup()
    renderWithProviders(<DirectoryForm id={null} onDone={() => {}} onDeleted={() => {}} />)
    expect(await screen.findByText(/^Next runs \(server time/)).toBeInTheDocument()
    expect(screen.getAllByRole('listitem').some(item => /16 Nov 2026/.test(item.textContent ?? ''))).toBe(true)
    await user.selectOptions(screen.getByLabelText('Schedule'), 'custom')
    const cron = screen.getByLabelText('Cron expression')
    await user.clear(cron)
    await user.type(cron, '61 * * * *')
    expect(await screen.findByText(/Invalid cron expression/)).toBeInTheDocument()
  })

  test('saves through the folder picker and reports field errors', async () => {
    const user = userEvent.setup()
    const saved: number[] = []
    renderWithProviders(<DirectoryForm id={null} onDone={id => { if (id !== null) saved.push(id) }} onDeleted={() => {}} />)
    await user.click(await screen.findByRole('button', { name: /downloads/ }))
    await user.click(await screen.findByRole('button', { name: 'Use this folder' }))
    expect(screen.getByLabelText('Folder')).toHaveValue('/data/downloads')
    expect(screen.getByLabelText('Name')).toHaveValue('downloads')
    await user.click(screen.getByRole('button', { name: 'Add directory' }))
    await waitFor(() => expect(saved).toEqual([5]))

    const directoryPath = screen.getByLabelText('Folder')
    await user.clear(directoryPath)
    await user.type(directoryPath, '/data/media')
    await user.click(screen.getByRole('button', { name: 'Add directory' }))
    expect(await screen.findByText('This folder has already been added.', { selector: '.field-error' })).toBeInTheDocument()
  })
})

describe('the directory menu', () => {
  test('hides and shows, and remembers it', async () => {
    const user = userEvent.setup()
    renderWithProviders(<Shell />)
    const toggle = screen.getByRole('button', { name: 'Show or hide the directories' })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(await screen.findByRole('link', { name: /Media/ })).toBeVisible()
    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(localStorage.getItem('disksprawl_sidebar_hidden')).toBe('1')
    await user.click(toggle)
    expect(localStorage.getItem('disksprawl_sidebar_hidden')).toBe('0')
  })

  test('shows each directory with its volume, freshness and problems', async () => {
    renderWithProviders(<Shell />)
    const media = await screen.findByRole('link', { name: /Media/ })
    expect(media).toHaveTextContent(/Updated \d+ hr\.? ago/)
    expect(within(media).getByRole('progressbar', { name: /Volume \d+% used/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Documents/ })).toHaveTextContent('Last scan failed')
  })

  test('invites to add a directory when there is none', async () => {
    server.use(http.get('/api/directories', () => HttpResponse.json({ items: [] })))
    renderWithProviders(<Shell />)
    expect(await screen.findByText('No directories yet.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add a directory' })).toBeInTheDocument()
  })
})
