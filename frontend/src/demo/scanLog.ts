/**
 * The log file of a demo scan, written as the server writes its scans' logs
 * (src/disksprawl/scan_logs.py): a header with the directory and the options, then each event with
 * its time in the server's time zone (every folder that could not be read among them), and how the
 * scan ended.
 */
import type { DirectoryOut, EngineOut, ScanOut } from '../api/schema.gen'

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

export interface ScanLogInput {
  scan: ScanOut
  directory: DirectoryOut
  engine?: EngineOut
  minMib: number
  /** Folders the scan stored, when it completed. */
  stored: number | null
  zone: string
}

/** The log of a scan that started: the name it downloads as, and its text. */
export function scanLog({ scan, directory, engine, minMib, stored, zone }: ScanLogInput): { name: string; text: string } {
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
  // The folders that could not be read, as the scan met them.
  scan.error_sample.forEach((error, index) => {
    const time = started + Math.round(((finished - started) * (index + 1)) / (scan.error_sample.length + 1))
    lines.push(`${at(time)}  Error: ${error.path ? `${directory.path}/${error.path}` : directory.path}: ${error.message}`)
  })
  const took = durationText(scan.duration_seconds)
  if (scan.status === 'completed') {
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
