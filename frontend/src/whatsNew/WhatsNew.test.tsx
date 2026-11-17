import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { demoStore } from '../demo/handlers'
import { renderWithProviders, resetApi, server } from '../test/render'
import { NOTES } from './notes'
import { WhatsNew } from './WhatsNew'

beforeAll(() => server.listen({ onUnhandledFrame: 'error' }))
beforeEach(() => { resetApi() })
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const ids = NOTES.map(note => note.id)
// A note of an earlier version: records that the visitor has been here before.
const EARLIER = '2026-01-01-earlier'

describe('what\'s new', () => {
  test('a new install has nothing to catch up on: every note is marked as read, none is shown', async () => {
    renderWithProviders(<WhatsNew />)
    await waitFor(() => expect(demoStore().whatsNew().seen).toEqual([...ids, ...(demoStore().whatsNew().seen?.includes(EARLIER) ? [EARLIER] : [])].toSorted()))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  test('shows the unread notes once and marks them as read when closed', async () => {
    demoStore().markWhatsNewSeen({ ids: [EARLIER] })
    renderWithProviders(<WhatsNew />)
    const dialog = await screen.findByRole('dialog', { name: "What's new" })
    expect(dialog).toHaveTextContent(NOTES[0].en.title)
    await userEvent.click(screen.getByRole('button', { name: 'Got it!' }))
    await waitFor(() => expect(demoStore().whatsNew().seen).toEqual([...ids, ...(demoStore().whatsNew().seen?.includes(EARLIER) ? [EARLIER] : [])].toSorted()))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  test('shows nothing when everything is read, and the bell lists every note by month', async () => {
    demoStore().markWhatsNewSeen({ ids })
    renderWithProviders(<WhatsNew />)
    await waitFor(() => expect(screen.getByRole('button', { name: "What's new" })).toBeEnabled())
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: "What's new" }))
    const dialog = await screen.findByRole('dialog', { name: "What's new" })
    expect(dialog).toHaveTextContent('November 2026')
    expect(dialog).toHaveTextContent(NOTES[0].en.title)
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(screen.getByRole('button', { name: "What's new" })).toHaveFocus())
  })

  test('keeps working when the answer cannot be saved', async () => {
    demoStore().markWhatsNewSeen({ ids: [EARLIER] })
    server.use(http.post('/api/whats-new/seen', () => HttpResponse.json({ code: 'internal_error' }, { status: 500 })))
    renderWithProviders(<WhatsNew />)
    await screen.findByRole('dialog')
    await userEvent.click(screen.getByRole('button', { name: 'Got it!' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })
})
