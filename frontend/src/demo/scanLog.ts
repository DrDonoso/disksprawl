/**
 * The log file of a demo scan, written as the server writes its scans' logs
 * (src/disksprawl/scan_logs.py): a header with the directory and the options, then each event with
 * its time in the server's time zone (its progress every ten seconds, every folder that could not
 * be read, the slow folders, the phases after the reading), and how the scan ended, with where its
 * time went. The demo has no real walk: it is simulated from the folders the scan found, each one
 * taking a share of the reading by its entries.
 */
import type { DirectoryOut, EngineOut, ScanOut } from '../api/schema.gen'
import type { Entry } from './model'
import { random } from './random'

// Characters a folder name never keeps, besides control characters: path separators and those
// Windows does not allow (as on the server).
const UNSAFE = '/\\:*?"<>|'
const UNITS = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB', 'EiB']
const PACES: Readonly<Record<string, string>> = { max: 'maximum pace', balanced: 'balanced pace', gentle: 'gentle pace' }
const MEASURES: Readonly<Record<string, string>> = { apparent: 'apparent size', disk: 'space on disk' }
const BINARIES: Readonly<Record<string, string>> = { du: '/usr/bin/du', ncdu: '/usr/local/bin/ncdu', gdu: '/usr/local/bin/gdu' }
const REASONS: Readonly<Record<string, string>> = {
  path_invalid: "the folder's path is not valid",
  path_not_found: 'the folder does not exist (it may not be mounted)',
  path_unreadable: 'the folder cannot be read: check its permissions',
  path_outside_root: 'the folder must be inside /data',
  path_not_directory: 'the path is a file, not a folder',
  path_unavailable: 'the folder is not available',
  engine_unavailable: 'its scan engine is not installed',
  engine_failed: 'the scan engine failed',
  scan_failed: 'the scan failed',
  internal_error: "unexpected server error: the container's log tells more",
}
// As on the server: a progress line every PROGRESS_SECONDS while the scan reads the folders, with
// its share done estimated like Live.estimate (scanning.py), and the folders that took long to
// read (engines/timing.py).
const PROGRESS_SECONDS = 10
const FOLDER_COST = 10
const SLOW_FOLDER_SECONDS = 5
const MAX_REPORTED_SLOW_FOLDERS = 1000
const SLOWEST_FOLDERS = 20
const TOP_FOLDERS = 15
const TOP_SUBFOLDERS = 5
const MIN_SHARE = 0.01
// The share of the time each pace rests (engines/native.py, engines/pacing.py).
const REST_SHARE: Readonly<Record<string, number>> = { max: 0, balanced: 0.25, gentle: 0.5 }

/** The folder of a directory's logs: its name, keeping only characters safe in a file name. */
export function folderName(name: string, id: number): string {
  const safe = [...name].map(character => {
    const code = character.charCodeAt(0)
    return code < 0x20 || code === 0x7f || UNSAFE.includes(character) ? '_' : character
  }).join('').replace(/^[ .]+|[ .]+$/g, '')
  const folder = [...safe].slice(0, 80).join('').replace(/[ .]+$/, '')
  return folder || `directory-${id}`
}

/** An attachment named `name`, with an ASCII fallback for older clients (RFC 6266). */
export function contentDisposition(name: string): string {
  const fallback = [...name].map(character => (character >= ' ' && character < '\x7f' ? character : '_')).join('')
  if (fallback === name) return `attachment; filename="${name}"`
  const encoded = encodeURIComponent(name).replace(/['()*!]/g, character => `%${character.charCodeAt(0).toString(16).toUpperCase()}`)
  return `attachment; filename="${fallback}"; filename*=UTF-8''${encoded}`
}

/** A moment in `zone`: its date, its time and its offset (`+01:00`). */
function local(time: number, zone: string): { date: string; time: string; offset: string } {
  const values = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {
    timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
    hourCycle: 'h23', timeZoneName: 'longOffset',
  }).formatToParts(new Date(time)).map(part => [part.type, part.value]))
  const offset = (values.timeZoneName ?? 'GMT').replace('GMT', '') || '+00:00'
  return { date: `${values.year}-${values.month}-${values.day}`, time: `${values.hour}:${values.minute}:${values.second}`, offset }
}

function sizeText(size: number): string {
  let value = Math.max(size, 0)
  let unit = 0
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024
    unit += 1
  }
  const digits = unit === 0 || value >= 100 ? 0 : value >= 10 ? 1 : 2
  return `${value.toFixed(digits)} ${UNITS[unit]}`
}

function durationText(seconds: number | null): string {
  const total = Math.max(Math.round(seconds ?? 0), 0)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = String(total % 60).padStart(2, '0')
  if (hours) return `${hours} h ${String(minutes).padStart(2, '0')} min ${secs} s`
  if (minutes) return `${minutes} min ${secs} s`
  return `${total} s`
}

const count = (value: number) => value.toLocaleString('en-US')
const plural = (value: number, one: string, other: string) => `${count(value)} ${value === 1 ? one : other}`
/** `+1 folder`, `+2,001 files`. */
const increase = (value: number, one: string, other: string) => `${value < 0 ? '' : '+'}${plural(value, one, other)}`

/** `0.4 s`, `12.3 s`, then as durationText: `4 min 05 s`. */
function secondsText(seconds: number): string {
  return seconds < 60 ? `${seconds.toFixed(1)} s` : durationText(seconds)
}

/** A path of the scan, relative to the scanned folder, below the folder as configured. */
const where = (root: string, path: string) => (path ? `${root}/${path}` : root)

/** Aligned, indented rows: the first cell to the left, the middle ones to the right, the last one (a path) as it is. */
function table(rows: string[][]): string[] {
  const widths = (rows[0] ?? []).map((_, column) => Math.max(...rows.map(row => row[column].length)))
  return rows.map(row => {
    const cells = row.map((cell, column) => {
      if (column === row.length - 1) return cell
      return column === 0 ? cell.padEnd(widths[column]) : cell.padStart(widths[column])
    })
    return `    ${cells.join('  ')}`
  })
}

/** A folder as the simulated walk reads it: what it holds itself, without its subfolders. */
interface Step {
  path: string
  files: number
  bytes: number
  /** Its files and subfolders. */
  entries: number
  /** The folders below it, at every depth. */
  dirs: number
  /** Its share of the reading, and how far the walk is once it is read (0 to 1). */
  weight: number
  end: number
}

/** How far a walk is: the folders it started, the files and bytes it read, and the folder it reads. */
interface Counters {
  dirs: number
  files: number
  bytes: number
  current: string
}

const parentOf = (path: string) => path.slice(0, Math.max(path.lastIndexOf('/'), 0))

/**
 * The folders the scan found, in the order a walk reads them: each one before its subfolders. Each
 * takes a share of the reading by its entries, give or take, and a few take much longer than the
 * rest, as on a busy disk or a network share.
 */
function walk(folders: ReadonlyMap<string, Entry>, seed: number): Step[] {
  const r = random(seed)
  const order = (path: string) => path.replaceAll('/', '\0')
  const paths = [...folders.keys()].sort((a, b) => (order(a) < order(b) ? -1 : order(a) > order(b) ? 1 : 0))
  // What the subfolders of each folder hold, to leave it out of the folder's own.
  const below = new Map<string, { size: number; files: number; folders: number }>()
  for (const path of paths) {
    if (!path) continue
    const entry = folders.get(path) as Entry
    const parent = below.get(parentOf(path)) ?? { size: 0, files: 0, folders: 0 }
    below.set(parentOf(path), { size: parent.size + entry.size, files: parent.files + entry.files, folders: parent.folders + 1 })
  }
  const steps = paths.map(path => {
    const entry = folders.get(path) as Entry
    const children = below.get(path) ?? { size: 0, files: 0, folders: 0 }
    const files = entry.files - children.files
    const entries = files + children.folders
    const weight = (1 + entries) * r.between(0.6, 1.4) * (r.chance(0.04) ? r.between(3, 12) : 1)
    return { path, files, bytes: entry.size - children.size, entries, dirs: entry.dirs, weight, end: 0 }
  })
  const total = steps.reduce((sum, step) => sum + step.weight, 0)
  let done = 0
  for (const step of steps) {
    step.weight /= total
    done += step.weight
    step.end = done
  }
  if (steps.length) steps[steps.length - 1].end = 1
  return steps
}

/** Where a walk is once it has done `share` of the reading (0 to 1). */
function reached(steps: readonly Step[], share: number): Counters {
  const found = steps.findIndex(step => step.end > share)
  const index = found < 0 ? steps.length - 1 : found
  const step = steps[index]
  const part = Math.min(Math.max((share - (step.end - step.weight)) / step.weight, 0), 1)
  const before = steps.slice(0, index)
  return {
    dirs: index + 1,
    files: before.reduce((sum, item) => sum + item.files, 0) + Math.round(step.files * part),
    bytes: before.reduce((sum, item) => sum + item.bytes, 0) + Math.round(step.bytes * part),
    current: step.path,
  }
}

/** The share done, estimated from the previous scan as the server does (Live.estimate): 99 % at most. */
function estimate(now: Counters, together: boolean, files: boolean, previous: Pick<ScanOut, 'dir_count' | 'file_count'> | null): number | null {
  if (!previous || previous.dir_count === null) return null
  const folders = previous.dir_count + 1
  let done = now.dirs
  let total = folders
  if (together) {
    if (previous.file_count === null) return null
    done = now.files + now.dirs
    total = previous.file_count + folders
  } else if (files && previous.file_count !== null) {
    done = now.files + FOLDER_COST * now.dirs
    total = previous.file_count + FOLDER_COST * folders
  }
  return done ? Math.min(done / total, 0.99) : null
}

/**
 * A progress line, as the server writes it (ScanLog.progress): du counts no files and tells no
 * folder, and ncdu with several threads counts the folders with the files.
 */
function progressLine(now: Counters, last: Counters, engine: string, threads: number, root: string, previous: Pick<ScanOut, 'dir_count' | 'file_count'> | null): string {
  const together = engine === 'ncdu' && threads > 1
  const files = engine !== 'du'
  const totals: string[] = []
  const changes: string[] = []
  if (together) {
    totals.push(plural(now.files + now.dirs, 'file or folder', 'files and folders'))
    changes.push(increase(now.files + now.dirs - last.files - last.dirs, 'file or folder', 'files and folders'))
  } else {
    totals.push(plural(now.dirs, 'folder', 'folders'))
    changes.push(increase(now.dirs - last.dirs, 'folder', 'folders'))
    if (files) {
      totals.push(plural(now.files, 'file', 'files'))
      changes.push(increase(now.files - last.files, 'file', 'files'))
    }
  }
  totals.push(`${sizeText(now.bytes)} read`)
  changes.push(`+${sizeText(now.bytes - last.bytes)}`)
  const share = estimate(now, together, files, previous)
  const done = share === null ? '' : `, about ${Math.round(100 * share)} % done`
  const reading = engine === 'du' ? '' : `; reading ${where(root, now.current)}`
  return `Progress: ${totals.join(', ')}${done}; in the last ${PROGRESS_SECONDS} s: ${changes.join(', ')}${reading}`
}

/**
 * The slowest folders to read, and the reading time of the first-level folders with their largest
 * subfolders, as the server lists them for the Native engine (scan_logs.py `folder_lines`). `work`
 * is the time the threads spent reading, added up.
 */
function folderTables(steps: readonly Step[], work: number, threads: number, root: string): string[] {
  const slowest = [...steps].sort((a, b) => b.weight - a.weight).slice(0, SLOWEST_FOLDERS)
  const lines = [
    '  Slowest folders to read, without their subfolders:',
    ...table(slowest.map(step => [secondsText(step.weight * work), plural(step.entries, 'entry', 'entries'), where(root, step.path)])),
  ]
  // Each folder with its subfolders: the time of every folder added to the folders above it.
  const total = new Map<string, number>()
  for (const step of steps) {
    for (let path = step.path; ; path = parentOf(path)) {
      total.set(path, (total.get(path) ?? 0) + step.weight * work)
      if (!path) break
    }
  }
  const reading = total.get('') ?? 0
  const seconds = (step: Step) => total.get(step.path) ?? 0
  const share = (value: number) => `${((100 * value) / reading).toFixed(1)} %`
  const largest = (parent: string) => steps.filter(step => step.path && parentOf(step.path) === parent).sort((a, b) => seconds(b) - seconds(a))
  const row = (step: Step, indent: string) => [indent + secondsText(seconds(step)), share(seconds(step)), plural(step.dirs + 1, 'folder', 'folders'), where(root, step.path)]
  const rows: string[][] = []
  for (const folder of largest('').slice(0, TOP_FOLDERS)) {
    rows.push(row(folder, ''))
    const subfolders = largest(folder.path).filter(subfolder => seconds(subfolder) >= MIN_SHARE * reading)
    rows.push(...subfolders.slice(0, TOP_SUBFOLDERS).map(subfolder => row(subfolder, '  ')))
  }
  // The scanned folder itself comes first in the walk.
  const own = steps[0].weight * work
  if (rows.length && own >= MIN_SHARE * reading) rows.push([secondsText(own), share(own), '', `${root}, without its subfolders`])
  if (!rows.length) return lines
  const title = `  Reading time by folder, with its subfolders${threads > 1 ? `, added up over the ${threads} threads` : ''}:`
  return [...lines, title, ...table(rows)]
}

export interface ScanLogInput {
  scan: ScanOut
  directory: DirectoryOut
  engine?: EngineOut
  minMib: number
  /** Folders the scan stored, when it completed. */
  stored: number | null
  zone: string
  /** Every folder the scan found (model.ts `snapshot`): its walk is simulated from them. */
  folders?: ReadonlyMap<string, Entry>
  /** The last scan that completed before it started, for the estimate of its share done. */
  previous?: Pick<ScanOut, 'dir_count' | 'file_count'> | null
  /** Older scans that lost their folders once it completed, keeping those of the latest `keep`. */
  pruned?: { scans: number; keep: number }
}

/** The log of a scan that started: the name it downloads as, and its text. */
export function scanLog({ scan, directory, engine, minMib, stored, zone, folders, previous = null, pruned }: ScanLogInput): { name: string; text: string } {
  const started = Date.parse(scan.started_at as string)
  const at = (time: number) => {
    const moment = local(time, zone)
    return `${moment.date} ${moment.time}${moment.offset}`
  }
  const start = local(started, zone)
  const name = `${folderName(directory.name, directory.id)}_${start.date}_${start.time.slice(0, 5).replace(':', '')}.log`
  const { options } = scan
  const threads = Math.max(1, Math.min(options.threads, engine?.max_threads ?? options.threads))
  const engineText = !engine?.available ? `${scan.engine} (not installed)`
    : BINARIES[scan.engine] ? `${scan.engine} ${engine.version} (${BINARIES[scan.engine]})` : `${scan.engine} (${engine.version})`
  const lines = [
    `DiskSprawl demo: scan ${scan.id}`,
    `Directory: ${directory.name} (id ${directory.id})`,
    `Folder: ${directory.path}`,
    `Started by: ${scan.trigger === 'scheduled' ? 'its schedule' : 'Scan now'}`,
    `Engine: ${engineText}`,
    `Options: ${[
      `${threads} ${threads === 1 ? 'thread' : 'threads'}`,
      PACES[options.pace] ?? options.pace,
      options.low_priority ? 'low priority' : 'normal priority',
      MEASURES[options.measure] ?? options.measure,
      options.one_filesystem ? 'one file system' : 'every file system',
    ].join(', ')}`,
    `Excluded: ${options.excludes.join(', ') || 'nothing'}`,
    `Smallest folder stored: ${minMib} MiB`,
    '',
    `${at(Date.parse(scan.queued_at))}  Queued`,
    `${at(started)}  Started`,
  ]
  if (scan.finished_at === null) return { name, text: `${lines.join('\n')}\n` }
  const finished = Date.parse(scan.finished_at)
  const completed = scan.status === 'completed'
  // The demo's scans fail before their engine starts (the folder is missing).
  const ran = scan.status !== 'failed'
  const duration = (finished - started) / 1000
  // After reading the folders, a completed scan adds up the results and saves them.
  const finish = completed ? Math.max(0.2, duration * 0.003) : 0
  const save = completed ? Math.max(0.3, duration * 0.006) : 0
  const reading = Math.max(duration - finish - save, 0)
  const rest = REST_SHARE[options.pace] ?? 0
  // A scan cut short had folders left to read.
  const walkSeconds = completed ? reading : reading / 0.9
  // The time the threads spent reading the folders, added up (Native).
  const work = walkSeconds * threads * (1 - rest)
  const steps = ran && folders?.size ? walk(folders, scan.id) : []
  const events: Array<[number, string]> = []
  const event = (seconds: number, text: string) => events.push([started + Math.round(seconds * 1000), text])
  if (ran && scan.engine === 'gdu') event(0.5, 'The engine reports no progress until it ends')
  if (steps.length && scan.engine !== 'gdu') {
    let last: Counters = { dirs: 0, files: 0, bytes: 0, current: '' }
    for (let time = PROGRESS_SECONDS; time < reading; time += PROGRESS_SECONDS) {
      const now = reached(steps, time / walkSeconds)
      event(time, progressLine(now, last, scan.engine, threads, directory.path, previous))
      last = now
    }
  }
  if (scan.engine === 'native') {
    steps
      .filter(step => step.weight * work >= SLOW_FOLDER_SECONDS && step.end * walkSeconds <= reading)
      .slice(0, MAX_REPORTED_SLOW_FOLDERS)
      .forEach(step => event(step.end * walkSeconds, `Slow folder: ${where(directory.path, step.path)} took ${secondsText(step.weight * work)} for ${plural(step.entries, 'entry', 'entries')}`))
  }
  // The folders that could not be read, as the scan met them.
  scan.error_sample.forEach((error, index) => {
    const step = steps.find(item => item.path === error.path)
    const time = step ? Math.min(step.end * walkSeconds, reading) : (duration * (index + 1)) / (scan.error_sample.length + 1)
    event(time, `Error: ${where(directory.path, error.path)}: ${error.message}`)
  })
  if (completed) {
    event(reading, 'Adding up the results')
    event(reading + finish, `Saving the results${stored === null ? '' : `: ${plural(stored, 'folder', 'folders')}`}`)
  }
  events.sort((a, b) => a[0] - b[0])
  lines.push(...events.map(([time, text]) => `${at(time)}  ${text}`))
  const took = durationText(scan.duration_seconds)
  if (completed) {
    const total = scan.total_bytes ?? 0
    lines.push(`${at(finished)}  Completed in ${took}`, `  Size: ${sizeText(total)} (${count(total)} bytes)`)
    if (scan.file_count !== null) lines.push(`  Files: ${count(scan.file_count)}`)
    lines.push(`  Folders: ${count(scan.dir_count ?? 0)}`)
    if (stored !== null) lines.push(`  Folders stored: ${count(stored)}`)
    lines.push(scan.error_count ? `  Errors: ${count(scan.error_count)} (listed above)` : '  Errors: none')
    if (scan.capacity_used !== null && scan.capacity_free !== null) {
      const available = scan.capacity_used + scan.capacity_free
      const percent = available ? Math.round((1000 * scan.capacity_used) / available) / 10 : 0
      lines.push(`  Volume: ${percent.toFixed(1)} % used (${sizeText(scan.capacity_used)} used, ${sizeText(scan.capacity_free)} free, ${sizeText(scan.capacity_total ?? 0)} in total)`)
    }
    lines.push('  Time:', `    Reading the folders: ${secondsText(reading)}`)
    if (rest) lines.push(`      Resting (${PACES[options.pace] ?? 'pace'}): ${secondsText(reading * rest)}, ${Math.round(100 * rest)} % of it`)
    lines.push(`    Adding up the results: ${secondsText(finish)}`, `    Saving the results: ${secondsText(save)}`)
    if (scan.engine === 'native' && steps.length && work > 0) lines.push(...folderTables(steps, work, threads, directory.path))
    if (pruned?.scans) {
      const seconds = Math.max(0.1, (stored ?? 0) * 0.002)
      lines.push(`${at(finished + Math.round(seconds * 1000))}  Removed the folders of ${plural(pruned.scans, 'older scan', 'older scans')} in ${secondsText(seconds)}, keeping those of the latest ${plural(pruned.keep, 'scan', 'scans')}`)
    }
  } else if (scan.status === 'failed') {
    const reason = REASONS[(scan.message ?? '').split(':')[0]]
    lines.push(`${at(finished)}  Failed after ${took}${reason ? `: ${reason}` : ''}`)
    if (scan.message) lines.push(`  Reason: ${scan.message}`)
  } else if (scan.status === 'cancelled') {
    lines.push(`${at(finished)}  Cancelled after ${took}`)
  } else if (scan.status === 'interrupted') {
    lines.push(`${at(finished)}  Interrupted: DiskSprawl stopped during the scan`)
  }
  return { name, text: `${lines.join('\n')}\n` }
}
