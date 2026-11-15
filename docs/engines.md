# Scan engines

Each directory is scanned by one of four engines, chosen in its settings. They measure the same
things and give the same totals; they differ in how they read the folders, how much they load the
machine and the storage, and what progress they can report while they run.

## Contents

- [Which engine to choose](#which-engine-to-choose)
- [How a scan runs](#how-a-scan-runs)
- [Native](#native)
- [du](#du)
- [ncdu](#ncdu)
- [gdu](#gdu)
- [Threads](#threads)
- [Pace](#pace)
- [Low priority](#low-priority)
- [What is measured](#what-is-measured)
- [Exclusions](#exclusions)
- [Progress and estimates](#progress-and-estimates)
- [What is stored](#what-is-stored)
- [Engine versions](#engine-versions)

## Which engine to choose

| Engine | Threads | Progress while scanning | Counts files | Load | Good for |
| --- | --- | --- | --- | --- | --- |
| **Native** | 1–16 | Live: folders, files, bytes and the folder being read | Yes | Some CPU in Python; memory grows with the number of folders | Any storage; the easiest to throttle. The default. |
| **du** | 1 | Finished folders | No | Very little CPU and memory | A steady, moderate load on any storage. |
| **ncdu** | 1–16 | Live; approximate with several threads | Yes | Like du with 1 thread; more memory with several | A light single-threaded scan that counts files, or a fast parallel one. |
| **gdu** | 1–8 | Only when it finishes | Yes | The most memory | SSDs and NFS shares, where reading in parallel pays off. |

Network shares are the usual worry. Walking folders over the network costs the NAS one request per
folder and per file, and the NAS carries that load whatever the engine. What you control is how
many requests are in flight (**threads**) and how often the scan stops (**pace**):

- **NAS over SMB**: 1 to 4 threads with the *Balanced* or *Gentle* pace. SMB is slow at listing
  folders; many threads mostly queue up on the NAS.
- **NAS over NFS**: 2 to 8 threads. NFS lists folders much faster than SMB.
- **Local SSD**: any engine at full speed; gdu is the fastest.
- **Local spinning disks**: 1 thread (gdu switches to its sequential mode) with low priority.

The first scan of a large share tells you how long it takes; the next ones show their progress
against it. If the NAS feels slow during a scan, lower the threads or pick a gentler pace.

## How a scan runs

Scans run **one at a time**, in the order they were requested, across all directories: two
directories on the same NAS never compete. A scheduled scan of a directory that is already queued
or running is not queued twice.

Every scan, whatever its engine, runs in a **separate process** (`python -m disksprawl.engines`),
started at low priority when the directory asks for it:

1. The process runs the engine: the native walk in its own threads, or `du`, `ncdu` or `gdu` as a
   child process started with a list of arguments, never through a shell.
2. It reads the engine's output as it arrives (NUL-separated records from du, the JSON export of
   ncdu and gdu, read as a stream) and builds the tree of folders with their size, files and
   subfolders.
3. Twice a second it reports its progress to the application, which shows it in the interface.
4. When it finishes, the application stores the totals and the folders worth keeping (see
   [What is stored](#what-is-stored)) and measures the volume's capacity.

Cancelling a scan stops its process (SIGTERM, then SIGKILL) and records the scan as cancelled. A
scan that was running when the application stopped is recorded as **interrupted** on the next
start. Folders and files that cannot be read, for example because of their permissions, do not
stop the scan: they are left out of the totals, counted, and the first 20 are listed with the
scan, which ends as "completed with N errors".

## Native

DiskSprawl's own engine, written in Python. It lists folders with `os.scandir` from as many
threads as the directory allows, and reads every entry with `lstat`.

- It reports **live progress**: folders, files and bytes counted so far, and the folder being
  read.
- Its **pace** is applied between folders: after each folder it rests in proportion to the time
  that folder took. *Balanced* rests a third of the working time (a quarter of the total) and
  *Gentle* as long as it worked (half of the total). The load is therefore spread evenly over the
  whole scan.
- It needs more CPU than du or ncdu, which only matters on very small machines or with millions of
  files.

## du

`du` from GNU coreutils, as installed by Debian in the image:

```text
du -0 -b|-B1 [-x] [--exclude=PATTERN …] -- FOLDER
```

- `-b` measures the apparent size and `-B1` the space on disk; `-x` stays on one file system; `-0`
  separates its records with NUL bytes, so any folder name is read correctly.
- It reads with **a single thread**: the threads setting does not apply.
- It prints each folder when it has finished it, deepest first, so the progress shows **finished
  folders**; the estimate uses the folder count of the previous scan.
- It does **not count files**: the file counts of du scans are empty.
- Read errors come from its error output and are counted like the other engines'.

## ncdu

[ncdu](https://dev.yorhel.nl/ncdu) 2, in export mode, from its official static build:

```text
ncdu --ignore-config -0 -o- [-x] [--exclude PATTERN …]              # 1 thread
ncdu --ignore-config -1 -o- [-x] [--exclude PATTERN …] -t THREADS   # several threads
```

- `-o-` writes the export, a JSON tree, to the standard output; `--ignore-config` ignores any
  configuration file; `-x` stays on one file system.
- With **1 thread** ncdu streams its export while it walks, so the progress is **live** and exact.
  It is as light as du and also counts files.
- With **several threads** (`-t`) it is faster, but builds the whole tree in memory and writes it
  at the end. Meanwhile, DiskSprawl reads the one-line progress it draws on a terminal (`-1`) to
  show approximate counters and the folder being read.
- ncdu measures both the apparent size and the space on disk in the same walk; DiskSprawl uses
  the one the directory asks for.

## gdu

[gdu](https://github.com/dundee/gdu), from its official static build:

```text
gdu -n -p -c -o- -l LOGFILE [-x] [-X IGNOREFILE] --sequential   # 1 thread
gdu -n -p -c -o- -l LOGFILE [-x] [-X IGNOREFILE] -m THREADS     # 2 to 8 threads
```

- `-n` runs without its interface, `-p` and `-c` without progress or colors, and `-o-` writes an
  export in ncdu's JSON format to the standard output; `-x` stays on one file system.
- It is built to read **in parallel**: with several threads (`-m`, the CPU cores it may use, 8 at
  most) it is usually the fastest engine on SSDs and NFS shares. On a NAS over SMB, many threads can
  overload it.
- With **1 thread** it uses its `--sequential` mode, meant for spinning disks.
- It reports **no progress until it finishes**; the interface shows the time elapsed and how long
  the previous scan took. Read errors come from its log file.
- It keeps the whole tree in memory until the end, so it uses the most memory.

## Threads

How many folders are read at the same time. More threads finish sooner, especially over the
network, but put more load on the disk or the NAS. The limits depend on the engine: Native and
ncdu accept up to 16, gdu up to 8 and du always uses 1. The default is 2.

## Pace

| Pace | Rests | Effect |
| --- | --- | --- |
| **Maximum** | Never | The scan runs as fast as the engine and the threads allow. |
| **Balanced** (default) | A quarter of the time | Takes about a third longer. |
| **Gentle** | Half of the time | Takes about twice as long, but the storage gets regular breaks. |

The Native engine rests between folders. du, ncdu and gdu do not know how to rest, so DiskSprawl
**freezes** their process at intervals: every 2 seconds it stops them with `SIGSTOP` and resumes
them with `SIGCONT`, for 0.5 seconds with *Balanced* and 1 second with *Gentle*. While frozen, the
process makes no requests to the storage. Cancelling a frozen scan resumes it first, so it can
stop.

## Low priority

When enabled (the default), the scan runs with the lowest CPU and disk priority of the machine:
`nice -n 19` and `ionice -c 3` (the *idle* I/O class), which yield to every other process. It
helps with local disks. With network folders the NAS carries the load and cannot see these
priorities: what matters there is the threads and the pace.

## What is measured

Every engine follows the rules of GNU du:

- **Apparent size** (the default) is the sum of the sizes of the files, as a file manager shows it.
  Folders themselves count nothing.
- **Space on disk** is the blocks actually allocated, folders included: compressed and sparse
  files take less than their size. Over SMB the NAS may report it approximately.
- **Hard links** count once. **Symbolic links** are never followed: a link counts as the small
  file it is, not as what it points to.
- **Stay on one file system** (`-x`, the default) skips folders on another file system: another
  disk, or a share mounted inside the directory.

Totals always include everything that could be read. The engines can disagree only on which
folder a hard-linked file counts in, because each one finds the links in a different order; the
totals do not change.

## Exclusions

Each directory has a list of names or patterns that are not counted, with `*` (any characters)
and `?` (one character), matched against the name of each file or folder, at any depth. By
default the snapshot folders `#snapshot`, `.snapshot` and `.zfs` are excluded, because they
contain other copies of the same files. Recycle bins (`#recycle`, `.Trash`…) are counted: they are
often what fills the disk.

Patterns cannot contain `/`, `\`, `[` or `]`. DiskSprawl translates them for each engine:
`--exclude` for du and ncdu, and anchored regular expressions in an ignore file for gdu.

## Progress and estimates

While a scan runs, the interface shows its phase (queued with its place in the queue, scanning, or
saving the results), the time elapsed and the engine's counters. When the directory has a
previous completed scan, it also estimates the **percentage done** and the **time left**: it
compares the bytes read so far with the previous total (or, for du, the finished folders with the
previous folder count). The estimate is marked as approximate and stays at 99 % at most until the
scan ends. The first scan of a directory shows its counters only. gdu shows the time elapsed and
how long the previous scan took.

## What is stored

For each completed scan DiskSprawl stores the totals (size, files, folders, errors and duration),
the options it used and the capacity of the volume. It also stores every folder of at least
**1 MiB** (adjustable under **⚙ Settings → General**) with its size, files and subfolders; smaller
folders and the files directly inside a folder appear together as "Loose files and small folders".
The totals always include them.

The last **30** completed scans of each directory keep their folders (also adjustable), for the
treemap and the comparisons; older scans keep only their totals, for the history. Deleting a
directory deletes its history, never your files.

## Engine versions

The image contains GNU du from Debian and the official static builds of ncdu 2.9.1 and gdu
5.39.0, downloaded and checked against their SHA-256 checksums by
[`scripts/install-engines.sh`](../scripts/install-engines.sh). At startup DiskSprawl looks for
each engine and asks for its version; an engine that is missing, or a `du` that is not GNU du,
appears disabled in the settings. `/api/engines` lists them with their versions and limits.
