import type { ReactElement, ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { setupServer } from 'msw/node'
import { I18nRoot } from '../i18n'
import { handlers, resetDemo } from '../demo/handlers'

/** The demo API as the backend of component tests (the same rules and error codes). */
export const server = setupServer(...handlers)

/** A fixed moment: 15 November 2026, 12:00 UTC. */
export const NOW = Date.UTC(2026, 10, 15, 12)

export function resetApi() {
  return resetDemo({ now: () => NOW })
}

export function renderWithProviders(ui: ReactElement, { route = '/', path = '*' }: { route?: string; path?: string } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => <I18nRoot>
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <Routes><Route path={path} element={children} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>
  </I18nRoot>
  return { client, ...render(ui, { wrapper }) }
}
