import { Suspense, lazy } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import './styles/global.css'

// The public demo's banner and welcome dialog (src/demo). The literal `import.meta.env.VITE_DEMO`
// check lets the bundler drop the demo, and MSW with it, from normal builds.
const DemoChrome = import.meta.env.VITE_DEMO === '1' ? lazy(() => import('./demo/DemoChrome')) : null

function render(container: HTMLElement) {
  createRoot(container).render(<App chrome={DemoChrome && <Suspense fallback={null}><DemoChrome /></Suspense>} />)
}

const container = document.getElementById('root')
// The demo answers /api in the browser: its service worker must run before the first request.
if (container && import.meta.env.VITE_DEMO === '1') {
  void import('./demo/browser').then(({ startDemo }) => startDemo(container)).then(
    started => { if (started) render(container) },
    (error: unknown) => console.error('The demo could not start:', error),
  )
} else if (container) {
  render(container)
}
