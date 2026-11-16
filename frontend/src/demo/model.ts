/**
 * The fictional home server of the demo: four directories whose folders change over a year. Every
 * folder size is a pure function of time, so any scan of the past can be rebuilt on demand and a
 * reload gives the same demo (relative to the current date).
 */
import { random, seedOf } from './random'

export const DAY = 86_400_000
const GiB = 2 ** 30
const MiB = 2 ** 20

/** Bytes of a folder (or of the loose files of a folder that also has subfolders) over time. */
export interface Leaf {
  path: string
  born: number
  died?: number
  /** Bytes when it appears, and growth per day up to `cap`. */
  size: number
  growth?: number
  cap?: number
  /** A sawtooth: it starts again from `size` every `period` milliseconds. */
  period?: number
  files: number
}

export interface Model {
  leaves: Leaf[]
  /** The volume the directory lives on (shared volumes have the same id). */
  volume: string
  /** Seconds a real scan takes with the directory's engine, for the history. */
  seconds: number
  /** Folders whose scans report a read error. */
  unreadable: string[]
}

export interface Volume {
  total: number
  /** Data on the volume outside every directory, growing slowly. */
  base: number
  growth: number
}

export interface Entry {
  size: number
  files: number
  dirs: number
}

export function leafSize(leaf: Leaf, time: number): number {
  if (time < leaf.born || (leaf.died !== undefined && time >= leaf.died)) return 0
  let age = time - leaf.born
  if (leaf.period) age %= leaf.period
  const grown = leaf.size + (leaf.growth ?? 0) * (age / DAY)
  return Math.round(leaf.cap ? Math.min(grown, leaf.cap) : grown)
}

const alive = (leaf: Leaf, time: number) => time >= leaf.born && (leaf.died === undefined || time < leaf.died)

/** Every folder of a directory at a moment: its size, files and subfolders (all depths). */
export function snapshot(model: Model, time: number): Map<string, Entry> {
  const folders = new Map<string, Entry>([['', { size: 0, files: 0, dirs: 0 }]])
  for (const leaf of model.leaves) {
    if (!alive(leaf, time)) continue
    const size = leafSize(leaf, time)
    const parts = leaf.path ? leaf.path.split('/') : []
    for (let depth = 0; depth <= parts.length; depth++) {
      const path = parts.slice(0, depth).join('/')
      let entry = folders.get(path)
      if (!entry) {
        entry = { size: 0, files: 0, dirs: 0 }
        folders.set(path, entry)
      }
      entry.size += size
      entry.files += leaf.files
    }
  }
  for (const path of folders.keys()) {
    if (!path) continue
    const parts = path.split('/')
    for (let depth = 0; depth < parts.length; depth++) {
      const ancestor = folders.get(parts.slice(0, depth).join('/'))
      if (ancestor) ancestor.dirs += 1
    }
  }
  return folders
}

/** Every folder that exists at some moment (the demo's file system for the folder picker). */
export function allFolders(model: Model): Set<string> {
  const folders = new Set<string>()
  for (const leaf of model.leaves) {
    const parts = leaf.path ? leaf.path.split('/') : []
    for (let depth = 1; depth <= parts.length; depth++) folders.add(parts.slice(0, depth).join('/'))
  }
  return folders
}

function day(now: number, daysAgo: number): number {
  return now - daysAgo * DAY
}

function dateName(time: number): string {
  return new Date(time).toISOString().slice(0, 10)
}

const MOVIES = [
  'A Quiet Engine (2018)', 'Paper Mountains (2016)', 'The Silent Orbit (2019)', 'Harbor Lights (2021)',
  'Northbound (2023)', 'Glass Rivers (2022)', 'Midnight Atlas (2020)', 'The Last Lighthouse (2017)',
  'Copper Skies (2015)', 'Seven Bridges (2024)', 'The Clockmaker (2014)', 'Low Tide (2022)',
  'Winter Orchard (2019)', 'The Cartographer (2021)', 'Static Bloom (2023)', 'Far Shore (2018)',
  'Lantern Road (2016)', 'Tin Kingdom (2020)', 'Salt and Ember (2025)', 'The Long Detour (2017)',
  'Moth Season (2022)', 'Saltwater Choir (2024)', 'Velvet Signal (2019)', 'Driftwood (2015)',
  'The Ninth Floor (2023)', 'Small Hours (2021)', 'Granite Bay (2018)', 'Hollow Pines (2020)',
  'The Weather Room (2025)', 'Echo Valley (2016)', 'Kite Road (2024)', 'Amber Coast (2022)',
  'Paper Planes (2019)', 'Cold Harbour (2017)', 'The Patient Fox (2023)', 'Night Garden (2026)',
]
const SHOWS = [
  'Star Gardeners', 'Coastline', 'The Archive', 'Northern Lights', 'Paper Trail', 'Quiet Valley',
  'Signal and Noise', 'Overland', 'The Long Table', 'Blue Hour',
]
const ARTISTS = [
  'Amber Fields', 'The Low Lanterns', 'Nora Vale', 'Glass Animals Club', 'Pale Harbor', 'Mira Sol',
  'The Night Ferries', 'Oskar Lind', 'Velvet Static', 'June Arbor', 'Cedar & Pine', 'The Radio Hearts',
  'Elena Brook', 'Moonlit Avenue', 'Hollow Drums', 'Iris Monday', 'The Paper Kites Band', 'Tom Ashby',
  'Northern Choir', 'Sea Shanty Club', 'Lumen', 'The Quiet Riot Trio', 'Ada Winter', 'Blue Canyon',
]
const PROJECTS = ['garden-planner', 'home-renovation', 'family-tree', 'recipes', 'travel-journal', 'bike-restoration']

export function mediaModel(now: number): Model {
  const r = random(7001)
  const leaves: Leaf[] = []
  for (const title of MOVIES) {
    const born = day(now, r.chance(0.3) ? r.between(5, 360) : r.between(370, 1500))
    leaves.push({ path: `Movies/${title}`, born, size: Math.round(r.between(3, 58) * GiB), files: r.int(1, 4) })
  }
  // A cleanup about four months ago: some old films went away.
  for (const leaf of leaves.slice(0, 6)) {
    if (leaf.born < day(now, 400)) leaf.died = day(now, 118)
  }
  for (const show of SHOWS) {
    const start = day(now, r.between(200, 1600))
    const seasons = r.int(2, 6)
    let born = start
    for (let season = 1; season <= seasons; season++) {
      const cap = Math.round(r.between(18, 85) * GiB)
      if (born < now) {
        leaves.push({
          path: `Series/${show}/Season ${String(season).padStart(2, '0')}`,
          born, size: Math.round(cap * 0.15), growth: (cap * 0.85) / 70, cap, files: r.int(6, 13),
          died: show === 'Overland' ? day(now, 55) : undefined,
        })
      }
      born += r.between(90, 220) * DAY
    }
  }
  for (const artist of ARTISTS) {
    leaves.push({
      path: `Music/${artist}`, born: day(now, r.between(400, 2000)), size: Math.round(r.between(0.4, 9) * GiB),
      growth: r.chance(0.3) ? 0.004 * GiB : 0, files: r.int(20, 260),
    })
  }
  for (const concert of ['Live at the Harbor', 'Winter Sessions', 'Open Air 2024', 'Acoustic Nights']) {
    leaves.push({ path: `Concerts/${concert}`, born: day(now, r.between(100, 900)), size: Math.round(r.between(15, 45) * GiB), files: r.int(1, 3) })
  }
  for (const show of ['Little Explorers', 'Bedtime Stories', 'The Busy Garden']) {
    leaves.push({ path: `Kids/${show}`, born: day(now, r.between(300, 1200)), size: Math.round(r.between(20, 60) * GiB), files: r.int(20, 60) })
  }
  // A season being recorded, growing every day.
  leaves.push({ path: 'Series/Blue Hour/Season 04', born: day(now, 20), size: 6 * GiB, growth: 1.6 * GiB, cap: 60 * GiB, files: 9 })
  leaves.push({ path: 'Downloads', born: day(now, 400), size: 2 * GiB, growth: 6 * GiB, period: 21 * DAY, files: 40 })
  leaves.push({ path: '#recycle', born: day(now, 330), size: 1 * GiB, growth: 1.4 * GiB, died: day(now, 20), files: 300 })
  leaves.push({ path: '#recycle', born: day(now, 20), size: 0.2 * GiB, growth: 0.9 * GiB, files: 25 })
  leaves.push({ path: '', born: day(now, 1500), size: 380 * MiB, files: 12 })
  return { leaves, volume: 'tank', seconds: 1800, unreadable: [] }
}

export function photosModel(now: number): Model {
  const r = random(7002)
  const leaves: Leaf[] = []
  const end = new Date(now)
  for (let year = 2014; year <= end.getUTCFullYear(); year++) {
    for (let month = 0; month < 12; month++) {
      const born = Date.UTC(year, month, 1)
      if (born > now) break
      const seasonal = month >= 5 && month <= 7 ? 1.7 : month === 11 ? 1.4 : 1
      const cap = Math.round(r.between(1.2, 14) * seasonal * (1 + (year - 2014) * 0.08) * GiB)
      const name = `${year}/${year}-${String(month + 1).padStart(2, '0')}`
      leaves.push({ path: name, born, size: Math.round(cap * 0.05), growth: (cap * 0.95) / 30, cap, files: r.int(80, 900) })
      if (r.chance(0.3)) {
        const videos = Math.round(r.between(2, 18) * GiB)
        leaves.push({ path: `${name}/Videos`, born: born + 3 * DAY, size: Math.round(videos * 0.2), growth: videos * 0.8 / 25, cap: videos, files: r.int(3, 40) })
      }
    }
  }
  leaves.push({ path: 'Inbox', born: day(now, 500), size: 0.5 * GiB, growth: 0.7 * GiB, period: 45 * DAY, files: 120 })
  leaves.push({ path: 'Albums/Wedding', born: day(now, 900), size: 38 * GiB, files: 1400 })
  leaves.push({ path: 'Albums/Graduation', born: day(now, 250), size: 12 * GiB, files: 520 })
  return { leaves, volume: 'tank', seconds: 3300, unreadable: [] }
}

export function backupsModel(now: number): Model {
  const r = random(7003)
  const leaves: Leaf[] = []
  const machines: [string, number, number, number][] = [
    // name, retention in weeks, smallest and largest snapshot in GiB
    ['laptop', 10, 150, 215],
    ['desktop', 6, 240, 330],
  ]
  for (const [machine, keep, low, high] of machines) {
    for (let week = 64; week >= 0; week--) {
      const born = day(now, week * 7 + 2.4)
      leaves.push({
        path: `${machine}/snapshot-${dateName(born)}`, born, died: born + keep * 7 * DAY,
        size: Math.round(r.between(low, high) * GiB), files: r.int(180_000, 260_000),
      })
    }
  }
  leaves.push({ path: 'phone/photo-sync', born: day(now, 700), size: 40 * GiB, growth: 0.12 * GiB, files: 9000 })
  leaves.push({ path: 'phone/messages', born: day(now, 700), size: 6 * GiB, growth: 0.02 * GiB, files: 2000 })
  leaves.push({ path: 'server/docker-volumes', born: day(now, 800), size: 30 * GiB, growth: 0.15 * GiB, files: 60_000 })
  leaves.push({ path: 'server/databases', born: day(now, 800), size: 8 * GiB, growth: 0.06 * GiB, files: 900 })
  leaves.push({ path: 'server/databases/postgres-data', born: day(now, 800), size: 14 * GiB, growth: 0.04 * GiB, files: 3000 })
  leaves.push({ path: 'server/configs', born: day(now, 800), size: 220 * MiB, files: 400 })
  leaves.push({ path: 'Time Machine', born: day(now, 600), size: 300 * GiB, growth: 1.2 * GiB, cap: 900 * GiB, files: 400_000 })
  return { leaves, volume: 'vault', seconds: 5400, unreadable: ['server/databases/postgres-data'] }
}

export function documentsModel(now: number): Model {
  const r = random(7004)
  const leaves: Leaf[] = []
  const current = new Date(now).getUTCFullYear()
  for (let year = 2016; year <= current; year++) {
    const born = Date.UTC(year, 0, 1)
    const yearly = year === current ? { growth: r.between(0.8, 1.5) * MiB, cap: 600 * MiB } : {}
    leaves.push({ path: `Invoices/${year}`, born, size: Math.round(r.between(80, 380) * MiB), files: r.int(40, 160), ...yearly })
    leaves.push({ path: `Taxes/${year}`, born, size: Math.round(r.between(100, 650) * MiB), files: r.int(10, 60) })
    leaves.push({ path: `Scans/${year}`, born, size: Math.round(r.between(300, 1900) * MiB), files: r.int(50, 400) })
  }
  leaves.push({ path: 'Manuals', born: day(now, 1500), size: 1.2 * GiB, growth: 0.002 * GiB, files: 300 })
  for (const project of PROJECTS) {
    const born = day(now, r.between(20, 900))
    leaves.push({ path: `Projects/${project}`, born, size: Math.round(r.between(0.2, 4) * GiB), growth: r.between(0, 0.03) * GiB, cap: 16 * GiB, files: r.int(30, 900) })
  }
  leaves.push({ path: '', born: day(now, 1500), size: 6 * GiB, files: 900 })
  return { leaves, volume: 'ssd', seconds: 160, unreadable: [] }
}

/** A plausible model for a folder added in the demo, seeded by its path. */
export function genericModel(path: string, now: number, volume: string): Model {
  const r = random(seedOf(path))
  const leaves: Leaf[] = []
  const folders = ['incoming', 'archive', 'projects', 'shared', 'old'].slice(0, r.int(3, 5))
  for (const folder of folders) {
    for (let index = 1; index <= r.int(2, 6); index++) {
      leaves.push({ path: `${folder}/item-${index}`, born: day(now, r.between(1, 600)), size: Math.round(r.between(0.1, 40) * GiB), files: r.int(5, 900) })
    }
  }
  return { leaves, volume, seconds: 600, unreadable: [] }
}

export const VOLUMES: Readonly<Record<string, Volume>> = {
  tank: { total: 10 * 1000 ** 4, base: 2.3 * 1000 ** 4, growth: 0.6 * GiB },
  vault: { total: 6 * 1000 ** 4, base: 0.22 * 1000 ** 4, growth: 0.1 * GiB },
  ssd: { total: 1 * 1000 ** 4, base: 0.38 * 1000 ** 4, growth: 0.2 * GiB },
}

/** Extra folders of the demo's /data, outside the four directories. */
export const OTHER_FOLDERS: Readonly<Record<string, { volume: string; folders: string[] }>> = {
  downloads: { volume: 'tank', folders: ['complete', 'incomplete', 'torrents'] },
  'vm-images': { volume: 'ssd', folders: ['templates', 'disks', 'isos'] },
}
