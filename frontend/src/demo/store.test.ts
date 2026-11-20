import { describe, expect, test } from 'vitest'
import { attachmentName } from '../api/client'
import { NOW } from '../test/clock'
import { DAY } from './model'
import { contentDisposition, folderName, scanLog } from './scanLog'
import { DemoError, DemoStore, biggestChanges } from './store'

function store(now = NOW) {
  let time = now
  const demo = new DemoStore({ now: () => time })
  return { demo, advance: (ms: number) => { time += ms } }
}

describe('the synthetic data', () => {
  test('is deterministic and relative to today', () => {
    const first = new DemoStore({ now: () => NOW }).list()
    const second = new DemoStore({ now: () => NOW }).list()
    expect(second).toEqual(first)
    expect(first.map(item => item.directory.name)).toEqual(['Media', 'Photos', 'Backups', 'Documents'])
    const later = new DemoStore({ now: () => NOW + 30 * DAY }).list()
    expect(Date.parse(later[0].last_scan?.finished_at ?? '')).toBeGreaterThan(NOW + 29 * DAY)
  })

  test('has a year of history, several engines, errors, failures and three volume levels', () => {
    const { demo } = store()
    const list = demo.list()
    for (const item of list) {
      const scans = demo.scanList(item.directory.id, null, 2000)
      const oldest = Math.min(...scans.map(scan => Date.parse(scan.finished_at ?? scan.queued_at)))
      expect(NOW - oldest, item.directory.name).toBeGreaterThan(330 * DAY)
    }
    expect(new Set(list.map(item => item.directory.engine))).toEqual(new Set(['native', 'du', 'ncdu', 'gdu']))
    const engines = new Set(demo.scanList(1, null, 2000).map(scan => scan.engine))
    expect(engines).toEqual(new Set(['native', 'gdu']))
    expect(list.find(item => item.directory.name === 'Backups')?.last_scan?.error_count).toBeGreaterThan(0)
    expect(list.find(item => item.directory.name === 'Documents')?.last_attempt?.status).toBe('failed')
    const percents = list.map(item => item.capacity?.percent ?? 0)
    expect(percents.some(percent => percent < 80)).toBe(true)
    expect(percents.some(percent => percent >= 80 && percent < 90)).toBe(true)
    expect(percents.some(percent => percent >= 90)).toBe(true)
    expect(list[0].capacity?.shared_with.map(other => other.name)).toEqual(['Photos'])
  })

  test('the latest scans keep their folders and the default comparison shows changes', () => {
    const { demo } = store()
    const tree = demo.tree(1, null, '')
    expect(tree.children.map(child => child.name)).toEqual(expect.arrayContaining(['Movies', 'Series', 'Music']))
    expect(tree.size).toBe(demo.summary(1).last_scan?.total_bytes)
    const diff = demo.diff(1, null, null, '')
    expect(diff.biggest.length).toBeGreaterThan(0)
    const movies = demo.diff(1, null, null, 'Movies')
    expect(movies.children.find(child => child.name === 'Sunday Machine (2026)')).toMatchObject({ before: null })
    const old = demo.scanList(1, null, 2000).filter(scan => scan.status === 'completed').at(-1)
    expect(old?.has_detail).toBe(false)
    expect(() => demo.tree(1, old?.id ?? 0, '')).toThrow(DemoError)
  })

  test('looks the same at any time of day', () => {
    // While a scheduled scan of Documents runs (every 6 hours), and once the daily scan of Media
    // has finished: the latest attempt of Documents still failed, and the film still shows up as
    // new between the last two scans of Media.
    for (const time of [Date.UTC(2026, 10, 15, 18, 1), Date.UTC(2026, 10, 16, 3, 50)]) {
      const demo = new DemoStore({ now: () => time })
      const list = demo.list()
      const at = new Date(time).toISOString()
      expect(list.find(item => item.directory.name === 'Documents')?.last_attempt?.status, at).toBe('failed')
      for (const item of list) expect(Date.parse(item.last_scan?.finished_at ?? ''), at).toBeLessThan(time)
      const movies = demo.diff(1, null, null, 'Movies')
      expect(movies.children.find(child => child.name === 'Sunday Machine (2026)'), at).toMatchObject({ before: null })
    }
  }, 30_000)
})

describe('simulated scans', () => {
  test('queue, report progress, then add a scan with small changes', () => {
    const { demo, advance } = store()
    const before = demo.summary(2).last_scan
    const first = demo.enqueue(2)
    expect(first.status).toBe('running')
    const queued = demo.enqueue(3)
    expect(queued).toMatchObject({ status: 'queued', position: 1 })
    expect(demo.enqueue(2).scan_id).toBe(first.scan_id)
    advance(3000)
    const progress = demo.summary(2).progress
    expect(progress).toMatchObject({ phase: 'scanning', counters: true })
    expect(progress?.percent).toBeGreaterThan(0.4)
    expect(progress?.current).toBeTruthy()
    advance(5000)
    const after = demo.summary(2)
    expect(after.progress).toBeNull()
    expect(after.last_scan?.id).toBe(first.scan_id)
    expect(after.last_scan?.total_bytes).not.toBe(before?.total_bytes)
    expect(demo.summary(3).progress?.status).toBe('running')
    expect(demo.cancel(queued.scan_id).status).toBe('cancelled')
    expect(() => demo.cancel(queued.scan_id)).toThrow(DemoError)
  })

  test('estimate from the folders and files of the previous scan, whatever the measure', () => {
    const { demo, advance } = store()
    // Photos measured the apparent size so far; its next scan measures the space on disk.
    const { id, position: _position, created_at: _created, updated_at: _updated, ...photos } = demo.get(2).directory
    demo.update(id, { ...photos, measure: 'disk' })
    // Photos counts folders and files (native), Documents only folders (du). A simulated scan reads
    // them evenly for 85 % of its 7 seconds.
    for (const directory of [2, 4]) {
      demo.enqueue(directory)
      advance(3000)
      expect(demo.summary(directory).progress?.percent).toBeCloseTo(3000 / 5950, 1)
      advance(5000)
    }
  })
})

describe('validation like the backend', () => {
  const body = { name: 'x', path: '/data/downloads' }

  function code(action: () => unknown): [string, Readonly<Record<string, string>> | undefined] {
    try {
      action()
    } catch (error) {
      if (error instanceof DemoError) return [error.code, error.fields]
      throw error
    }
    throw new Error('no error')
  }

  test('paths, duplicates, cron, threads and exclusions', () => {
    const { demo } = store()
    expect(code(() => demo.create({ ...body, path: '/etc' }))).toEqual(['path_outside_root', { path: 'path_outside_root' }])
    expect(code(() => demo.create({ ...body, path: '/data/nope' }))).toEqual(['path_not_found', { path: 'path_not_found' }])
    expect(code(() => demo.create({ ...body, path: '/data/media' }))).toEqual(['duplicate_path', { path: 'duplicate_path' }])
    expect(code(() => demo.create({ ...body, cron: '61 * * * *' }))).toEqual(['invalid_cron', { cron: 'invalid_cron' }])
    expect(code(() => demo.create({ ...body, engine: 'gdu', threads: 9 }))).toEqual(['threads_out_of_range', { threads: 'threads_out_of_range' }])
    expect(code(() => demo.create({ ...body, excludes: ['a/b'] }))).toEqual(['invalid_request', { excludes: 'invalid_exclude' }])
    expect(demo.create({ ...body, engine: 'du', threads: 6 }).directory.threads).toBe(1)
    expect(demo.cronPreview('0 3 * * 1').next_runs.map(run => new Date(run).getUTCDay())).toEqual([1, 1, 1])
  })
})

describe('biggest changes', () => {
  test('a folder that explains its parent\'s change is listed instead of the parent', () => {
    const entry = (size: number) => ({ size, files: 0, dirs: 0 })
    const before = new Map([['', entry(1000)], ['media', entry(600)], ['media/movies', entry(500)], ['old', entry(300)]])
    const after = new Map([['', entry(1300)], ['media', entry(1100)], ['media/movies', entry(500)], ['media/series', entry(550)]])
    expect(biggestChanges(before, after, '')).toEqual([
      { path: 'media/series', before: null, after: 550 },
      { path: 'old', before: 300, after: null },
    ])
  })
})

describe('scan logs', () => {
  test('every scan that started has one until it expires, written as the server writes it', () => {
    const { demo } = store()
    const backups = demo.summary(3).last_scan
    if (!backups) throw new Error('no scan of Backups')
    expect(backups.has_log).toBe(true)
    const log = demo.scanLog(backups.id)
    const started = new Date(backups.started_at as string).toISOString()
    expect(log.name).toBe(`Backups_${started.slice(0, 10)}_${started.slice(11, 13)}${started.slice(14, 16)}.log`)
    const lines = log.text.split('\n')
    expect(lines.slice(0, 9)).toEqual([
      `DiskSprawl demo: scan ${backups.id}`,
      'Directory: Backups (id 3)',
      'Folder: /data/backups',
      `Started by: ${backups.trigger === 'scheduled' ? 'its schedule' : 'Scan now'}`,
      'Engine: ncdu 2.9.1 (/usr/local/bin/ncdu)',
      'Options: 1 thread, balanced pace, low priority, apparent size, one file system',
      'Excluded: #snapshot, .snapshot, .zfs',
      'Smallest folder stored: 1 MiB',
      '',
    ])
    expect(lines[9]).toMatch(/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\+00:00 {2}Queued$/)
    const errors = lines.filter(line => line.includes('  Error: '))
    expect(errors).toHaveLength(backups.error_count)
    expect(errors[0]).toMatch(/ {2}Error: \/data\/backups\/.+: read error$/)
    expect(lines).toContain(`  Errors: ${backups.error_count} (listed above)`)
    expect(lines.find(line => line.includes('  Completed in '))).toBeDefined()

    // A queued scan has not started: no log until it does.
    expect(demo.scan(demo.enqueue(2).scan_id).has_log).toBe(true)
    const queued = demo.enqueue(4).scan_id
    expect(demo.scan(queued).has_log).toBe(false)
    expect(() => demo.scanLog(queued)).toThrow(expect.objectContaining({ code: 'scan_log_not_found', status: 404 }))
    expect(() => demo.scanLog(999_999)).toThrow(expect.objectContaining({ code: 'scan_not_found', status: 404 }))

    // Logs expire `scan_log_days` days after their scan ended.
    const old = demo.scanList(3, null, 2000).filter(scan => Date.parse(scan.finished_at ?? '') < NOW - 2 * DAY)
    expect(old.length).toBeGreaterThan(0)
    expect(old.every(scan => scan.has_log === (Date.parse(scan.finished_at as string) >= NOW - 30 * DAY))).toBe(true)
    demo.saveSettings({ ...demo.settings, scan_log_days: 1 })
    expect(demo.scan(old[0].id).has_log).toBe(false)
    expect(() => demo.saveSettings({ ...demo.settings, scan_log_days: 3651 })).toThrow(expect.objectContaining({ fields: { scan_log_days: 'invalid' } }))
  })

  test('names, time zones and endings', () => {
    const { demo } = store()
    const failed = demo.summary(4).last_attempt
    if (!failed) throw new Error('no failed scan of Documents')
    const { directory } = demo.get(4)
    const nepal = scanLog({ scan: failed, directory: { ...directory, name: 'Docs: 2026/11 "final"' }, minMib: 1, stored: null, zone: 'Asia/Kathmandu' })
    const started = new Date(Date.parse(failed.started_at as string) + (5 * 60 + 45) * 60_000).toISOString()
    expect(nepal.name).toBe(`Docs_ 2026_11 _final__${started.slice(0, 10)}_${started.slice(11, 13)}${started.slice(14, 16)}.log`)
    const lines = nepal.text.trimEnd().split('\n')
    expect(lines.slice(-2)).toEqual([
      expect.stringMatching(/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\+05:45 {2}Failed after [\d hmin]+s: the folder does not exist \(it may not be mounted\)$/),
      '  Reason: path_not_found',
    ])
    expect(folderName('..', 7)).toBe('directory-7')
    expect(folderName(' .hidden. ', 7)).toBe('hidden')
    const name = 'Fotos de niños_2026-11-15_1200.log'
    expect(contentDisposition(name)).toBe(`attachment; filename="Fotos de ni_os_2026-11-15_1200.log"; filename*=UTF-8''Fotos%20de%20ni%C3%B1os_2026-11-15_1200.log`)
    expect(attachmentName(contentDisposition(name))).toBe(name)
    expect(attachmentName(contentDisposition('Media_2026-11-15_0300.log'))).toBe('Media_2026-11-15_0300.log')
  })
})

describe("what's new", () => {
  test('records the notes read, sorted and without repeats, checked like the API', () => {
    const { demo } = store()
    expect(demo.whatsNew()).toEqual({ seen: null })
    const read = ['2026-11-16-a', '2026-12-01-b']
    expect(demo.markWhatsNewSeen({ ids: ['2026-12-01-b', '2026-11-16-a'] })).toEqual({ seen: read })
    expect(demo.markWhatsNewSeen({ ids: ['2026-11-16-a'] })).toEqual({ seen: read })
    const invalid: Array<[unknown, string]> = [
      [[], 'too_short'],
      [Array.from({ length: 501 }, (_, index) => `2026-01-01-n${index}`), 'too_long'],
      ['2026-11-16-a', 'list_type'],
      [[7], 'string_type'],
      [['notes'], 'string_pattern_mismatch'],
      [[`2026-11-16-${'a'.repeat(70)}`], 'string_too_long'],
      [undefined, 'missing'],
    ]
    for (const [ids, type] of invalid) {
      expect(() => demo.markWhatsNewSeen({ ids } as never)).toThrow(expect.objectContaining({ status: 422, fields: { ids: type } }))
    }
    expect(() => demo.markWhatsNewSeen({ ids: read, all: true } as never)).toThrow(expect.objectContaining({ fields: { all: 'extra_forbidden' } }))
  })
})
