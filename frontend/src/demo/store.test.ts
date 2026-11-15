import { describe, expect, test } from 'vitest'
import { DAY } from './model'
import { DemoError, DemoStore, biggestChanges } from './store'

const NOW = Date.UTC(2026, 10, 15, 12)

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
