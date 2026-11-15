/**
 * The demo's in-memory server: directories, a year of scan history, volumes, settings and a scan
 * queue that simulates a few seconds of progress. It answers like the backend (src/disksprawl):
 * the same validation, error codes and read models, from the synthetic models of model.ts.
 */
import { Cron } from 'croner'
import type {
  BrowseOut, CapacityHistoryOut, CapacityOut, CronPreviewOut, DiffChangeOut, DiffOut, DirectoryIn, DirectoryOut, DirectorySummaryOut,
  EngineOut, ProgressOut, ScanOut, SettingsIn, SettingsOut, TreeOut,
} from '../api/schema.gen'
import {
  DAY, OTHER_FOLDERS, VOLUMES, allFolders, backupsModel, documentsModel, genericModel, leafSize, mediaModel, photosModel,
  snapshot, type Entry, type Model,
} from './model'
import { random } from './random'

export class DemoError extends Error {
  constructor(public code: string, public status = 422, public fields?: Readonly<Record<string, string>>) {
    super(code)
  }
}

type Engine = DirectoryOut['engine']
type Status = ScanOut['status']

interface DemoDirectory {
  directory: DirectoryOut
  model: Model
}

interface DemoScan extends Omit<ScanOut, 'has_detail'> {
  /** Simulated scans: when the queue started it, and how long it runs (ms). */
  run?: { start: number; length: number }
}

export const ROOT = '/data'
const MIB = 2 ** 20
const SCAN_SECONDS = 7
const KEEP_SCANS_DEFAULT = 30
const MAX_THREADS: Readonly<Record<Engine, number>> = { native: 16, du: 1, ncdu: 16, gdu: 8 }
const SAMPLE_EVERY = 15 * 60_000
const BIGGEST_CHANGES = 10
const ENGINES: EngineOut[] = [
  { id: 'native', available: true, version: 'Python 3.14', max_threads: 16, progress: 'live' },
  { id: 'du', available: true, version: '9.7', max_threads: 1, progress: 'folders' },
  { id: 'ncdu', available: true, version: '2.9.1', max_threads: 16, progress: 'live' },
  { id: 'gdu', available: true, version: '5.39.0', max_threads: 8, progress: 'none' },
]

function iso(time: number): string {
  return new Date(time).toISOString()
}

function timezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Etc/UTC'
  } catch {
    return 'Etc/UTC'
  }
}

/** Cron validation and next runs like the backend: five fields, standard days of the week, both
 * day fields must match. */
export function cronRuns(expression: string, count: number, after: number, zone = timezone()): number[] {
  const text = expression.trim().split(/\s+/).join(' ')
  if (!text || text.length > 120 || text.split(' ').length !== 5) throw new DemoError('invalid_cron', 422, { cron: 'invalid_cron' })
  try {
    const job = new Cron(text, { timezone: zone, paused: true, domAndDow: true })
    return job.nextRuns(count, new Date(after)).map(date => date.getTime())
  } catch {
    throw new DemoError('invalid_cron', 422, { cron: 'invalid_cron' })
  }
}

function options(directory: DirectoryOut): ScanOut['options'] {
  const { engine, threads, pace, low_priority, measure, one_filesystem, excludes } = directory
  return { engine, threads, pace, low_priority, measure, one_filesystem, excludes: [...excludes] }
}

export interface StoreOptions {
  now?: () => number
}

export class DemoStore {
  readonly clock: () => number
  readonly start: number
  readonly timezone = timezone()
  private directories: DemoDirectory[] = []
  private scans: DemoScan[] = []
  private nextScanId = 1
  private nextDirectoryId = 1
  private changes = 0
  settings: Omit<SettingsOut, 'timezone'> = {
    capacity_interval_minutes: 15, keep_scans: KEEP_SCANS_DEFAULT, min_entry_mib: 1, warn_percent: 80, critical_percent: 90,
  }

  constructor({ now = Date.now }: StoreOptions = {}) {
    this.clock = now
    this.start = now()
    this.seed()
  }

  // The year of history

  private add(directory: Omit<DirectoryOut, 'id' | 'position' | 'created_at' | 'updated_at'>, model: Model, created: number): DemoDirectory {
    const id = this.nextDirectoryId++
    const entry: DemoDirectory = {
      directory: { ...directory, id, position: id, created_at: iso(created), updated_at: iso(created) },
      model,
    }
    this.directories.push(entry)
    return entry
  }

  private seed() {
    const now = this.start
    const year = now - 365 * DAY
    const base = {
      enabled: true, low_priority: true, measure: 'apparent' as const, one_filesystem: true,
      excludes: ['#snapshot', '.snapshot', '.zfs'],
    }
    const media = this.add({ ...base, name: 'Media', path: '/data/media', cron: '0 3 * * *', engine: 'gdu', threads: 4, pace: 'balanced' }, mediaModel(now), year)
    const photos = this.add({ ...base, name: 'Photos', path: '/data/photos', cron: '30 4 * * *', engine: 'native', threads: 2, pace: 'gentle' }, photosModel(now), year)
    const backups = this.add({ ...base, name: 'Backups', path: '/data/backups', cron: '0 5 * * 0', engine: 'ncdu', threads: 1, pace: 'balanced' }, backupsModel(now), year)
    const documents = this.add({ ...base, name: 'Documents', path: '/data/documents', cron: '0 */6 * * *', engine: 'du', threads: 1, pace: 'max', low_priority: false }, documentsModel(now), year)
    const r = random(7100)
    // Something to compare right away: a film added between the last two scans of Media.
    const mediaRuns = cronRuns(media.directory.cron ?? '0 3 * * *', 2, now - 2 * DAY, this.timezone)
    const latest = Math.max(...mediaRuns.filter(time => time < now - 3600_000))
    media.model.leaves.push({ path: 'Movies/Sunday Machine (2026)', born: latest - 9 * 3600_000, size: 31 * 2 ** 30, files: 2 })
    for (const { directory, model } of [media, photos, backups, documents]) {
      const runs = cronRuns(directory.cron ?? '0 3 * * *', 2000, year, this.timezone).filter(time => time < now)
      runs.forEach((time, index) => {
        const started = time + r.between(4, 80) * 1000
        const seconds = model.seconds * r.between(0.85, 1.2)
        const finished = started + seconds * 1000
        if (finished >= now) return
        // Media moved from the native engine to gdu five months ago.
        const engine: Engine = directory.id === media.directory.id && time < now - 150 * DAY ? 'native' : directory.engine
        let status: Status = 'completed'
        let message: string | null = null
        const fromEnd = runs.length - 1 - index
        if (directory.id === documents.directory.id && fromEnd === 0) {
          status = 'failed'
          message = 'path_not_found'
        } else if (directory.id === media.directory.id && fromEnd === 61) status = 'interrupted'
        else if (directory.id === photos.directory.id && fromEnd === 23) status = 'cancelled'
        const scan = this.record(directory, model, {
          started, finished, status, message, engine,
          trigger: index % 17 === 5 ? 'manual' : 'scheduled',
          threads: engine === 'native' && directory.engine !== 'native' ? 2 : directory.threads,
        })
        if (status === 'completed' && model.unreadable.length && fromEnd % 3 === 0) {
          scan.error_count = model.unreadable.length
          scan.error_sample = model.unreadable.map(path => ({ path, message: 'read error' }))
        }
      })
    }
  }

  private record(directory: DirectoryOut, model: Model, scan: {
    started: number; finished: number; status: Status; message: string | null; engine: Engine; trigger: ScanOut['trigger']; threads: number
  }): DemoScan {
    const done = scan.status === 'completed'
    const root = done ? snapshot(model, scan.finished).get('') : undefined
    const volume = done ? this.volumeAt(model.volume, scan.finished) : undefined
    const entry: DemoScan = {
      id: this.nextScanId++,
      directory_id: directory.id,
      status: scan.status,
      trigger: scan.trigger,
      engine: scan.engine,
      options: { ...options(directory), engine: scan.engine, threads: scan.threads },
      queued_at: iso(scan.started - 1000),
      started_at: iso(scan.started),
      finished_at: iso(scan.finished),
      duration_seconds: Math.round((scan.finished - scan.started) / 100) / 10,
      total_bytes: root?.size ?? null,
      file_count: root && scan.engine !== 'du' ? root.files : null,
      dir_count: root?.dirs ?? null,
      error_count: 0,
      error_sample: [],
      message: scan.message,
      capacity_total: volume?.total ?? null,
      capacity_used: volume?.used ?? null,
      capacity_free: volume?.free ?? null,
    }
    this.scans.push(entry)
    return entry
  }

  // Volumes

  private volumeAt(volume: string, time: number): { total: number; used: number; free: number } {
    const spec = VOLUMES[volume] ?? VOLUMES.tank
    let used = spec.base + spec.growth * ((time - this.start) / DAY)
    for (const { model } of this.directories) {
      if (model.volume !== volume) continue
      for (const leaf of model.leaves) used += leafSize(leaf, time)
    }
    used = Math.round(Math.min(used, spec.total * 0.995))
    return { total: spec.total, used, free: spec.total - used }
  }

  private capacity(entry: DemoDirectory): CapacityOut {
    const measured = Math.floor(this.clock() / SAMPLE_EVERY) * SAMPLE_EVERY
    const { total, used, free } = this.volumeAt(entry.model.volume, measured)
    return {
      total, used, free, percent: percentOf(used, free), measured_at: iso(measured),
      shared_with: this.directories
        .filter(other => other !== entry && other.model.volume === entry.model.volume)
        .map(other => ({ id: other.directory.id, name: other.directory.name })),
    }
  }

  capacityHistory(id: number, days: number): CapacityHistoryOut {
    const entry = this.get(id)
    const end = Math.floor(this.clock() / SAMPLE_EVERY) * SAMPLE_EVERY
    const begin = Math.max(end - days * DAY, Date.parse(entry.directory.created_at))
    const step = Math.max(SAMPLE_EVERY, Math.ceil((end - begin) / 400 / SAMPLE_EVERY) * SAMPLE_EVERY)
    const points = []
    for (let time = begin; time <= end; time += step) {
      const { total, used, free } = this.volumeAt(entry.model.volume, time)
      points.push({ measured_at: iso(time), total, used, free, percent: percentOf(used, free) })
    }
    return { points }
  }

  // The queue: simulated scans run one at a time, each a few seconds long.

  private tick() {
    const now = this.clock()
    for (;;) {
      const running = this.scans.find(scan => scan.status === 'running')
      if (running?.run && now >= running.run.start + running.run.length) {
        this.complete(running, running.run.start + running.run.length)
        continue
      }
      if (running) return
      const queued = this.scans.filter(scan => scan.status === 'queued').sort((a, b) => a.id - b.id)[0]
      if (!queued) return
      const lastEnd = Math.max(0, ...this.scans.filter(scan => scan.run && scan.finished_at).map(scan => Date.parse(scan.finished_at as string)))
      const start = Math.max(Date.parse(queued.queued_at), lastEnd)
      if (start > now) return
      queued.status = 'running'
      queued.started_at = iso(start)
      queued.run = { start, length: SCAN_SECONDS * 1000 }
    }
  }

  private complete(scan: DemoScan, finished: number) {
    const entry = this.directories.find(item => item.directory.id === scan.directory_id)
    if (!entry) return
    this.change(entry.model, finished)
    const root = snapshot(entry.model, finished).get('') as Entry
    const volume = this.volumeAt(entry.model.volume, finished)
    Object.assign(scan, {
      status: 'completed', finished_at: iso(finished), duration_seconds: SCAN_SECONDS,
      total_bytes: root.size, file_count: scan.engine === 'du' ? null : root.files, dir_count: root.dirs,
      capacity_total: volume.total, capacity_used: volume.used, capacity_free: volume.free,
      error_count: entry.model.unreadable.length,
      error_sample: entry.model.unreadable.map(path => ({ path, message: 'read error' })),
    })
  }

  /** A small change before each simulated scan ends: something new appears, or something goes. */
  private change(model: Model, time: number) {
    const r = random(9000 + this.changes++)
    const living = model.leaves.filter(leaf => leaf.path && leafSize(leaf, time - 1000) > 0)
    if (r.chance(0.35) && living.length > 4) {
      r.pick(living).died = time - 1000
      return
    }
    const parent = r.pick(living)?.path.split('/').slice(0, -1).join('/') ?? ''
    model.leaves.push({
      path: `${parent ? `${parent}/` : ''}New in the demo ${this.changes}`, born: time - 1000,
      size: Math.round(r.between(0.5, 30) * 2 ** 30), files: r.int(1, 200),
    })
  }

  enqueue(id: number, trigger: ScanOut['trigger'] = 'manual'): ProgressOut {
    this.tick()
    const entry = this.get(id)
    let scan = this.scans.find(item => item.directory_id === id && (item.status === 'queued' || item.status === 'running'))
    if (!scan) {
      const now = this.clock()
      scan = {
        id: this.nextScanId++, directory_id: id, status: 'queued', trigger, engine: entry.directory.engine,
        options: options(entry.directory), queued_at: iso(now), started_at: null, finished_at: null,
        duration_seconds: null, total_bytes: null, file_count: null, dir_count: null, error_count: 0,
        error_sample: [], message: null, capacity_total: null, capacity_used: null, capacity_free: null,
      }
      this.scans.push(scan)
      this.tick()
    }
    return this.progress(scan)
  }

  cancel(scanId: number): ScanOut {
    this.tick()
    const scan = this.scans.find(item => item.id === scanId)
    if (!scan) throw new DemoError('scan_not_found', 404)
    if (scan.status !== 'queued' && scan.status !== 'running') throw new DemoError('scan_not_active', 409)
    const now = this.clock()
    scan.status = 'cancelled'
    scan.finished_at = iso(now)
    scan.duration_seconds = scan.started_at ? Math.round((now - Date.parse(scan.started_at)) / 100) / 10 : null
    delete scan.run
    this.tick()
    return this.out(scan)
  }

  private previous(scan: DemoScan): DemoScan | undefined {
    return this.completed(scan.directory_id).find(item => item.id !== scan.id)
  }

  progress(scan: DemoScan): ProgressOut {
    const queued = scan.status === 'queued'
    const base = {
      scan_id: scan.id, directory_id: scan.directory_id, queued_at: scan.queued_at, engine: scan.engine,
      pace: scan.options.pace, threads: scan.options.threads,
    }
    const previous = this.previous(scan)
    if (queued || !scan.run) {
      const position = this.scans.filter(item => item.status === 'queued').sort((a, b) => a.id - b.id).indexOf(scan) + 1
      return {
        ...base, status: queued ? 'queued' : 'running', phase: queued ? 'queued' : 'scanning', position: queued ? position : null,
        started_at: scan.started_at, elapsed_seconds: 0, counters: false, dirs: 0, files: null, bytes: 0, current: null,
        percent: null, remaining_seconds: null, previous_duration_seconds: previous?.duration_seconds ?? null,
      }
    }
    const elapsed = Math.max(0, this.clock() - scan.run.start)
    const scanning = scan.run.length * 0.85
    const share = Math.min(1, elapsed / scanning)
    const entry = this.directories.find(item => item.directory.id === scan.directory_id)
    const target = entry ? snapshot(entry.model, this.clock()) : new Map<string, Entry>()
    const root = target.get('') ?? { size: 0, files: 0, dirs: 0 }
    const paths = [...target.keys()].filter(Boolean)
    const counters = scan.engine !== 'gdu'
    const comparable = previous && previous.options.measure === scan.options.measure
    const percent = counters && comparable && elapsed < scanning ? Math.min(0.99, share) : null
    return {
      ...base, status: 'running', phase: elapsed < scanning ? 'scanning' : 'saving', position: null, started_at: scan.started_at,
      elapsed_seconds: Math.round(elapsed / 100) / 10, counters,
      dirs: counters ? Math.round(root.dirs * share) : 0,
      files: counters && scan.engine !== 'du' ? Math.round(root.files * share) : null,
      bytes: counters ? Math.round(root.size * share) : 0,
      current: (scan.engine === 'native' || scan.engine === 'ncdu') && paths.length ? paths[Math.min(paths.length - 1, Math.floor(share * paths.length))] : null,
      percent: percent === null ? null : Math.round(percent * 10000) / 10000,
      remaining_seconds: percent !== null && elapsed >= 2000 ? Math.round((scanning - elapsed) / 1000) : null,
      previous_duration_seconds: previous?.duration_seconds ?? null,
    }
  }

  queue(): ProgressOut[] {
    this.tick()
    return this.scans
      .filter(scan => scan.status === 'queued' || scan.status === 'running')
      .sort((a, b) => (a.status === b.status ? a.id - b.id : a.status === 'running' ? -1 : 1))
      .map(scan => this.progress(scan))
  }

  // Scans and their folders

  private completed(id: number): DemoScan[] {
    return this.scans
      .filter(scan => scan.directory_id === id && scan.status === 'completed')
      .sort((a, b) => Date.parse(b.finished_at as string) - Date.parse(a.finished_at as string) || b.id - a.id)
  }

  /** The latest `keep_scans` completed scans of a directory keep their folders. */
  private detailed(id: number): DemoScan[] {
    return this.completed(id).slice(0, this.settings.keep_scans)
  }

  out(scan: DemoScan): ScanOut {
    const { run: _run, ...rest } = scan
    const detail = scan.status === 'completed' && this.detailed(scan.directory_id).includes(scan)
    return { ...rest, has_detail: detail }
  }

  scan(id: number): ScanOut {
    this.tick()
    const scan = this.scans.find(item => item.id === id)
    if (!scan) throw new DemoError('scan_not_found', 404)
    return this.out(scan)
  }

  scanList(id: number, days: number | null, limit: number): ScanOut[] {
    this.tick()
    this.get(id)
    const since = days === null ? -Infinity : this.clock() - days * DAY
    return this.scans
      .filter(scan => scan.directory_id === id && (scan.finished_at === null || Date.parse(scan.finished_at) >= since))
      .sort((a, b) => Date.parse(b.queued_at) - Date.parse(a.queued_at) || b.id - a.id)
      .slice(0, limit)
      .map(scan => this.out(scan))
  }

  private detailedScan(id: number, scanId: number | null): DemoScan {
    const detailed = this.detailed(id)
    if (scanId === null) {
      if (!detailed.length) throw new DemoError('no_scan_yet', 404)
      return detailed[0]
    }
    const scan = this.scans.find(item => item.id === scanId)
    if (!scan || scan.directory_id !== id) throw new DemoError('scan_not_found', 404)
    if (!detailed.includes(scan)) throw new DemoError('scan_without_detail', 404)
    return scan
  }

  /** The stored folders of a scan: the root and every folder of at least the minimum size. */
  private folders(id: number, scan: DemoScan): Map<string, Entry> {
    const all = snapshot(this.get(id).model, Date.parse(scan.finished_at as string))
    const minimum = this.settings.min_entry_mib * MIB
    return new Map([...all].filter(([path, entry]) => path === '' || entry.size >= minimum))
  }

  tree(id: number, scanId: number | null, rawPath: string): TreeOut {
    this.tick()
    this.get(id)
    const scan = this.detailedScan(id, scanId)
    const path = cleanPath(rawPath)
    const folders = this.folders(id, scan)
    const own = folders.get(path)
    if (!own) throw new DemoError('path_not_found', 404)
    const children = childrenOf(folders, path)
      .map(child => ({ ...child, has_children: childrenOf(folders, child.path).length > 0 }))
      .sort((a, b) => b.size - a.size || a.name.localeCompare(b.name))
    return {
      scan: this.out(scan), path, size: own.size, files: scan.engine === 'du' ? null : own.files, dirs: own.dirs,
      other_size: Math.max(own.size - children.reduce((sum, child) => sum + child.size, 0), 0),
      children: children.map(child => ({ ...child, files: scan.engine === 'du' ? null : child.files })),
    }
  }

  diff(id: number, fromId: number | null, toId: number | null, rawPath: string): DiffOut {
    this.tick()
    this.get(id)
    const after = this.detailedScan(id, toId)
    let before: DemoScan
    if (fromId !== null) before = this.detailedScan(id, fromId)
    else {
      const earlier = this.detailed(id).filter(scan => scan.id !== after.id && Date.parse(scan.finished_at as string) <= Date.parse(after.finished_at as string))
      if (!earlier.length) throw new DemoError('no_previous_scan', 404)
      before = earlier[0]
    }
    const path = cleanPath(rawPath)
    const old = this.folders(id, before)
    const now = this.folders(id, after)
    if (!old.has(path) && !now.has(path)) throw new DemoError('path_not_found', 404)
    const names = new Set([...childrenOf(old, path), ...childrenOf(now, path)].map(child => child.name))
    const children = [...names].map(name => {
      const child = path ? `${path}/${name}` : name
      return {
        name, path: child, before: old.get(child)?.size ?? null, after: now.get(child)?.size ?? null,
        has_children: childrenOf(old, child).length > 0 || childrenOf(now, child).length > 0,
      }
    }).sort((a, b) => Math.max(b.before ?? 0, b.after ?? 0) - Math.max(a.before ?? 0, a.after ?? 0) || a.name.localeCompare(b.name))
    const beforeTotal = old.get(path)?.size ?? null
    const afterTotal = now.get(path)?.size ?? null
    return {
      from_scan: this.out(before), to_scan: this.out(after), path, before: beforeTotal, after: afterTotal,
      other_before: Math.max((beforeTotal ?? 0) - children.reduce((sum, child) => sum + (child.before ?? 0), 0), 0),
      other_after: Math.max((afterTotal ?? 0) - children.reduce((sum, child) => sum + (child.after ?? 0), 0), 0),
      children, biggest: biggestChanges(old, now, path),
    }
  }

  // Directories

  get(id: number): DemoDirectory {
    const entry = this.directories.find(item => item.directory.id === id)
    if (!entry) throw new DemoError('directory_not_found', 404)
    return entry
  }

  private nextRun(directory: DirectoryOut): string | null {
    if (!directory.enabled || !directory.cron) return null
    return iso(cronRuns(directory.cron, 1, this.clock(), this.timezone)[0])
  }

  summary(id: number): DirectorySummaryOut {
    this.tick()
    const entry = this.get(id)
    const scans = this.scans.filter(scan => scan.directory_id === id)
    const completed = this.completed(id)[0]
    const finished = scans
      .filter(scan => ['completed', 'failed', 'cancelled', 'interrupted'].includes(scan.status))
      .sort((a, b) => Date.parse(b.finished_at as string) - Date.parse(a.finished_at as string) || b.id - a.id)[0]
    const active = scans.find(scan => scan.status === 'queued' || scan.status === 'running')
    return {
      directory: { ...entry.directory, excludes: [...entry.directory.excludes] },
      last_scan: completed ? this.out(completed) : null,
      last_attempt: finished && finished.status !== 'completed' ? this.out(finished) : null,
      progress: active ? this.progress(active) : null,
      capacity: this.capacity(entry),
      next_run: this.nextRun(entry.directory),
    }
  }

  list(): DirectorySummaryOut[] {
    return [...this.directories]
      .sort((a, b) => a.directory.position - b.directory.position || a.directory.id - b.directory.id)
      .map(entry => this.summary(entry.directory.id))
  }

  /** The demo's /data: the four directories' folders plus a few more. */
  folderSet(): Set<string> {
    const folders = new Set<string>([ROOT])
    for (const { directory, model } of this.directories) {
      folders.add(directory.path)
      for (const folder of allFolders(model)) folders.add(`${directory.path}/${folder}`)
    }
    for (const [name, { folders: inner }] of Object.entries(OTHER_FOLDERS)) {
      folders.add(`${ROOT}/${name}`)
      for (const folder of inner) folders.add(`${ROOT}/${name}/${folder}`)
    }
    return folders
  }

  browse(rawPath: string | null): BrowseOut {
    const path = rawPath ? normalize(rawPath) : ROOT
    if (path !== ROOT && !path.startsWith(`${ROOT}/`)) throw new DemoError('path_outside_root', 422, { path: 'path_outside_root' })
    const folders = this.folderSet()
    if (!folders.has(path)) throw new DemoError('path_not_found', 422, { path: 'path_not_found' })
    const entries = [...folders]
      .filter(folder => folder.startsWith(`${path}/`) && !folder.slice(path.length + 1).includes('/'))
      .map(folder => ({ name: folder.slice(path.length + 1), path: folder }))
      .sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()) || a.name.localeCompare(b.name))
    return { root: ROOT, path, parent: path === ROOT ? null : path.slice(0, path.lastIndexOf('/')), entries }
  }

  private validate(body: DirectoryIn, current: number | null): Omit<DirectoryOut, 'id' | 'position' | 'created_at' | 'updated_at'> {
    const fields: Record<string, string> = {}
    const name = typeof body.name === 'string' ? body.name.split(/\s+/).filter(Boolean).join(' ') : ''
    if (!name) fields.name = typeof body.name === 'string' && body.name.length ? 'name_required' : 'invalid'
    else if (name.length > 60) fields.name = 'invalid'
    const engine = body.engine ?? 'native'
    if (!MAX_THREADS[engine]) fields.engine = 'invalid'
    const threads = body.threads ?? 2
    if (!Number.isInteger(threads) || threads < 1 || threads > 16) fields.threads = 'invalid'
    const excludes: string[] = []
    for (const raw of body.excludes ?? ['#snapshot', '.snapshot', '.zfs']) {
      const pattern = raw.trim()
      if (!pattern) continue
      if (pattern.length > 255 || /[/\\[\]\0]/.test(pattern)) fields.excludes = 'invalid_exclude'
      else if (!excludes.includes(pattern)) excludes.push(pattern)
    }
    if (excludes.length > 50) fields.excludes = 'too_many_excludes'
    if (Object.keys(fields).length) throw new DemoError('invalid_request', 422, fields)
    if (!body.path.startsWith('/') || body.path.includes('\0')) throw new DemoError('path_invalid', 422, { path: 'path_invalid' })
    const path = normalize(body.path)
    if (path !== ROOT && !path.startsWith(`${ROOT}/`)) throw new DemoError('path_outside_root', 422, { path: 'path_outside_root' })
    if (!this.folderSet().has(path)) throw new DemoError('path_not_found', 422, { path: 'path_not_found' })
    const duplicate = this.directories.find(item => item.directory.path === path)
    if (duplicate && duplicate.directory.id !== current) throw new DemoError('duplicate_path', 409, { path: 'duplicate_path' })
    const limit = MAX_THREADS[engine]
    if (limit > 1 && threads > limit) throw new DemoError('threads_out_of_range', 422, { threads: 'threads_out_of_range' })
    const cron = body.cron === null || body.cron === undefined || !body.cron.trim() ? null : body.cron.trim().split(/\s+/).join(' ')
    if (cron) cronRuns(cron, 1, this.clock(), this.timezone)
    return {
      name, path, enabled: body.enabled ?? true, cron: body.cron === undefined ? '0 3 * * *' : cron, engine,
      threads: limit === 1 ? 1 : threads, pace: body.pace ?? 'balanced', low_priority: body.low_priority ?? true,
      measure: body.measure ?? 'apparent', one_filesystem: body.one_filesystem ?? true, excludes,
    }
  }

  private modelFor(path: string): Model {
    for (const { directory, model } of this.directories) {
      if (path === directory.path) return model
    }
    const top = path.slice(ROOT.length + 1).split('/')[0]
    return genericModel(path, this.clock(), OTHER_FOLDERS[top]?.volume ?? 'tank')
  }

  create(body: DirectoryIn): DirectorySummaryOut {
    const clean = this.validate(body, null)
    const now = this.clock()
    const position = Math.max(0, ...this.directories.map(item => item.directory.position)) + 1
    const id = this.nextDirectoryId++
    this.directories.push({
      directory: { ...clean, id, position, created_at: iso(now), updated_at: iso(now) },
      model: this.modelFor(clean.path),
    })
    return this.summary(id)
  }

  update(id: number, body: DirectoryIn): DirectorySummaryOut {
    const entry = this.get(id)
    const clean = this.validate(body, id)
    if (clean.path !== entry.directory.path) entry.model = this.modelFor(clean.path)
    entry.directory = { ...entry.directory, ...clean, updated_at: iso(this.clock()) }
    return this.summary(id)
  }

  remove(id: number) {
    this.get(id)
    this.directories = this.directories.filter(item => item.directory.id !== id)
    this.scans = this.scans.filter(scan => scan.directory_id !== id)
    this.tick()
  }

  order(ids: number[]): DirectorySummaryOut[] {
    const known = this.directories.map(item => item.directory.id).sort((a, b) => a - b)
    if (JSON.stringify([...ids].sort((a, b) => a - b)) !== JSON.stringify(known)) {
      throw new DemoError('invalid_order', 422, { ids: 'invalid_order' })
    }
    ids.forEach((id, index) => { this.get(id).directory.position = index + 1 })
    return this.list()
  }

  saveSettings(body: SettingsIn): SettingsOut {
    const limits: Readonly<Record<keyof SettingsIn, [number, number]>> = {
      capacity_interval_minutes: [1, 1440], keep_scans: [1, 1000], min_entry_mib: [1, 1024], warn_percent: [1, 99], critical_percent: [2, 100],
    }
    const fields: Record<string, string> = {}
    for (const [key, [low, high]] of Object.entries(limits)) {
      const value = (body as unknown as Record<string, unknown>)[key]
      if (typeof value !== 'number' || !Number.isInteger(value) || value < low || value > high) fields[key] = 'invalid'
    }
    if (Object.keys(fields).length) throw new DemoError('invalid_request', 422, fields)
    if (body.critical_percent <= body.warn_percent) throw new DemoError('thresholds_out_of_order', 422, { critical_percent: 'thresholds_out_of_order' })
    this.settings = { ...body }
    return this.settingsOut()
  }

  settingsOut(): SettingsOut {
    return { ...this.settings, timezone: this.timezone }
  }

  cronPreview(expression: string): CronPreviewOut {
    const normalized = expression.trim().split(/\s+/).join(' ')
    if (!normalized) throw new DemoError('invalid_cron', 422, { cron: 'invalid_cron' })
    return {
      expression: normalized,
      timezone: this.timezone,
      next_runs: cronRuns(normalized, 3, this.clock(), this.timezone).map(iso),
    }
  }

  engines(): EngineOut[] {
    return ENGINES.map(engine => ({ ...engine }))
  }
}

function percentOf(used: number, free: number): number {
  return used + free ? Math.round((1000 * used) / (used + free)) / 10 : 0
}

function normalize(path: string): string {
  const parts: string[] = []
  for (const part of path.split('/')) {
    if (!part || part === '.') continue
    if (part === '..') parts.pop()
    else parts.push(part)
  }
  return `/${parts.join('/')}`
}

function cleanPath(path: string): string {
  const parts = path.split('/').filter(Boolean)
  if (parts.some(part => part === '.' || part === '..')) throw new DemoError('path_not_found', 404)
  return parts.join('/')
}

function childrenOf(folders: Map<string, Entry>, path: string): (Entry & { name: string; path: string })[] {
  const prefix = path ? `${path}/` : ''
  const children = []
  for (const [folder, entry] of folders) {
    if (!folder || !folder.startsWith(prefix)) continue
    const rest = folder.slice(prefix.length)
    if (rest && !rest.includes('/')) children.push({ ...entry, name: rest, path: folder })
  }
  return children
}

/** The folders below `path` that changed the most, preferring a subfolder that explains at
 * least 90 % of its parent's change (the backend's rule). */
export function biggestChanges(before: Map<string, Entry>, after: Map<string, Entry>, path: string): DiffChangeOut[] {
  const prefix = path ? `${path}/` : ''
  const paths = new Set([...before.keys(), ...after.keys()].filter(folder => folder !== path && folder.startsWith(prefix)))
  const delta = (folder: string) => (after.get(folder)?.size ?? 0) - (before.get(folder)?.size ?? 0)
  const children = new Map<string, string[]>()
  for (const folder of paths) {
    const parent = folder.slice(0, folder.lastIndexOf('/'))
    if (paths.has(parent)) children.set(parent, [...(children.get(parent) ?? []), folder])
  }
  const explained = (folder: string) => {
    const change = delta(folder)
    return (children.get(folder) ?? []).some(child => delta(child) * change > 0 && Math.abs(delta(child)) >= 0.9 * Math.abs(change))
  }
  return [...paths]
    .filter(folder => delta(folder) !== 0 && !explained(folder))
    .sort((a, b) => Math.abs(delta(b)) - Math.abs(delta(a)) || a.localeCompare(b))
    .slice(0, BIGGEST_CHANGES)
    .map(folder => ({ path: folder, before: before.get(folder)?.size ?? null, after: after.get(folder)?.size ?? null }))
}
