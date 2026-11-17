export type NoteKind = 'new' | 'improved' | 'fixed'
export const KINDS: readonly NoteKind[] = ['new', 'improved', 'fixed']

export interface NoteText {
  title: string
  body: string
}

export interface NoteFile {
  kind: NoteKind
  en: NoteText
  es: NoteText
}

export interface Note extends NoteFile {
  /** The file name without `.json`: `YYYY-MM-DD-slug`. */
  id: string
}

/** The pattern of `NoteId` in the API (up to 80 characters). */
export const NOTE_ID = /^\d{4}-\d{2}-\d{2}-[a-z0-9]+(-[a-z0-9]+)*$/

// No type argument on purpose: Vitest's cache of transformed modules handles this form.
const files = import.meta.glob('./notes/*.json', { eager: true, import: 'default' }) as Record<string, NoteFile>

/** Every note, newest first. */
export const NOTES: readonly Note[] = Object.entries(files)
  .map(([path, file]) => ({ ...file, id: path.slice('./notes/'.length, -'.json'.length) }))
  .toSorted((a, b) => b.id.localeCompare(a.id))

/** Notes by kind (new things first, then improvements, then fixes), newest first inside each. */
export function byKind(notes: readonly Note[]): Note[] {
  return KINDS.flatMap(kind => notes.filter(note => note.kind === kind))
}

/** Notes by month (`YYYY-MM-01`), newest first. */
export function byMonth(notes: readonly Note[]): Array<{ month: string; notes: Note[] }> {
  const months = new Map<string, Note[]>()
  for (const note of notes) {
    const month = `${note.id.slice(0, 7)}-01`
    months.set(month, [...(months.get(month) ?? []), note])
  }
  return [...months].map(([month, list]) => ({ month, notes: list }))
}
