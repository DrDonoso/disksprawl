import { describe, expect, it } from 'vitest'
import { KINDS, NOTES, NOTE_ID, byKind, byMonth, type Note } from './notes'

const raw = import.meta.glob('./notes/*.json', { eager: true, import: 'default' }) as Record<string, Record<string, unknown>>

describe('note files', () => {
  it('has at least one note', () => expect(NOTES.length).toBeGreaterThan(0))

  for (const [path, file] of Object.entries(raw)) {
    const id = path.slice('./notes/'.length, -'.json'.length)
    it(`${id} is valid`, () => {
      expect(id).toMatch(NOTE_ID)
      expect(id.length).toBeLessThanOrEqual(80)
      const date = id.slice(0, 10)
      expect(new Date(`${date}T00:00:00Z`).toISOString().slice(0, 10)).toBe(date)
      expect(Object.keys(file).toSorted()).toEqual(['en', 'es', 'kind'])
      expect(KINDS).toContain(file.kind)
      for (const language of ['en', 'es']) {
        const text = file[language] as { title: string; body: string }
        expect(Object.keys(text).toSorted()).toEqual(['body', 'title'])
        for (const [field, limit] of [['title', 70], ['body', 300]] as const) {
          const value = text[field]
          expect(value, `${language}.${field}`).toBe(value.trim().replace(/\s+/g, ' '))
          expect(value.length, `${language}.${field}`).toBeGreaterThan(0)
          expect(value.length, `${language}.${field}`).toBeLessThanOrEqual(limit)
        }
      }
    })
  }
})

describe('grouping', () => {
  const note = (id: string, kind: Note['kind']): Note => ({ id, kind, en: { title: id, body: id }, es: { title: id, body: id } })
  const notes = [note('2026-12-02-a', 'fixed'), note('2026-12-01-b', 'new'), note('2026-11-20-c', 'improved'), note('2026-11-19-d', 'new')]

  it('lists new things first, then improvements and fixes, newest first inside each', () => {
    expect(byKind(notes).map(item => item.id)).toEqual(['2026-12-01-b', '2026-11-19-d', '2026-11-20-c', '2026-12-02-a'])
  })

  it('groups by month, newest first', () => {
    expect(byMonth(notes).map(group => [group.month, group.notes.length])).toEqual([['2026-12-01', 2], ['2026-11-01', 2]])
  })
})
