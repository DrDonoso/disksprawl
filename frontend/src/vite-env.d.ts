/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** `'1'` in the public demo build (`--mode demo`, frontend/.env.demo). */
  readonly VITE_DEMO?: string
}

declare module '*.module.css' {
  const classes: Readonly<Record<string, string>>
  export default classes
}

declare module '*?raw' {
  const content: string
  export default content
}
