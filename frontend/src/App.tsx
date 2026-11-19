import { Component, type ReactNode } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes } from 'react-router'
import { errorMessage, queryClient } from './api/client'
import { useAuthStatus } from './api/hooks'
import { I18nRoot, useI18n } from './i18n'
import { DirectoryPage } from './components/DirectoryPage'
import { Home, NotFound } from './components/Home'
import { Login } from './components/Login'
import { Shell } from './components/Shell'
import { Notice } from './components/ui'
import { PushProvider } from './push/PushContext'

function CrashNotice() {
  const { t } = useI18n()
  return <section className="card" style={{ padding: 20, margin: 20 }} role="alert">
    <h1>{t('shell.crashTitle')}</h1>
    <p>{t('shell.crashText')}</p>
    <button type="button" className="button" onClick={() => location.reload()}>{t('shell.reload')}</button>
  </section>
}

class Boundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  componentDidCatch(error: Error) {
    console.error('The page could not be rendered:', error)
  }
  render() {
    return this.state.failed ? <CrashNotice /> : this.props.children
  }
}

/** The sign-in form when a password is configured and there is no session. */
function AuthGate({ children }: { children: ReactNode }) {
  const { t } = useI18n()
  const auth = useAuthStatus()
  if (auth.isPending) return <p className="muted" style={{ padding: 20 }}>{t('shell.loading')}</p>
  if (auth.error) return <div style={{ padding: 20 }}><Notice error>{errorMessage(auth.error)}</Notice></div>
  if (auth.data.enabled && !auth.data.authenticated) return <Login />
  return children
}

export function App({ chrome }: { chrome?: ReactNode }) {
  return <I18nRoot>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Boundary>
          <AuthGate>
            <PushProvider>
              <Routes>
                <Route element={<Shell banner={chrome} />}>
                  <Route index element={<Home />} />
                  <Route path="directories/:id" element={<DirectoryPage />} />
                  <Route path="*" element={<NotFound />} />
                </Route>
              </Routes>
            </PushProvider>
          </AuthGate>
        </Boundary>
      </BrowserRouter>
    </QueryClientProvider>
  </I18nRoot>
}
