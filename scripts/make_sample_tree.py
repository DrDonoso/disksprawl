"""Creates a deterministic sample tree to try DiskSprawl with: media, photos, backups and documents.

    python scripts/make_sample_tree.py /tmp/sample            # create (or recreate) the tree
    python scripts/make_sample_tree.py /tmp/sample --mutate 1 # apply change set 1 (2, 3… for more)

Large files are sparse: they have a realistic apparent size but take almost no disk space. A few
small files hold real bytes, so "space on disk" is not zero. The tree also has hard links, a
symbolic link, a recycle bin and a `.snapshot` folder (excluded by default), so every engine rule
is exercised. Everything is synthetic; nothing is read from the machine.
"""

import argparse
import os
import random
import shutil
from pathlib import Path

GIB = 1 << 30
MIB = 1 << 20

MOVIES = [
    "The Silent Orbit (2019)", "Harbor Lights (2021)", "Paper Mountains (2016)",
    "Northbound (2023)", "A Quiet Engine (2018)", "Glass Rivers (2022)", "Midnight Atlas (2020)",
    "The Last Lighthouse (2017)",
]
SERIES = {"Star Gardeners": 3, "Coastline": 2, "The Archive": 4}
MACHINES = ["laptop", "desktop", "media-server"]


def sparse(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        handle.truncate(size)


def real(path: Path, size: int, rng: random.Random) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rng.randbytes(size))


def create(root: Path, seed: int) -> None:
    rng = random.Random(seed)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    for title in MOVIES:
        sparse(root / "Media" / "Movies" / title / f"{title}.mkv", rng.randint(2, 9) * GIB)
        real(root / "Media" / "Movies" / title / f"{title}.srt", rng.randint(40, 90) * 1024, rng)
    for show, seasons in SERIES.items():
        for season in range(1, seasons + 1):
            for episode in range(1, rng.randint(6, 10) + 1):
                name = f"{show} S{season:02d}E{episode:02d}.mkv"
                sparse(root / "Media" / "Series" / show / f"Season {season}" / name,
                       rng.randint(350, 1400) * MIB)
    for year in (2022, 2023, 2024, 2025):
        for month in range(1, 13):
            folder = root / "Photos" / str(year) / f"{year}-{month:02d}"
            for number in range(rng.randint(3, 25)):
                sparse(folder / f"IMG_{year}{month:02d}{number:04d}.jpg", rng.randint(2, 9) * MIB)
            if rng.random() < 0.25:
                sparse(folder / f"VID_{year}{month:02d}.mp4", rng.randint(200, 1800) * MIB)
    for machine in MACHINES:
        for week in range(1, rng.randint(4, 8) + 1):
            sparse(root / "Backups" / machine / f"week-{week:02d}.tar.zst", rng.randint(8, 40) * GIB)
    for folder in ("Invoices", "Manuals", "Taxes/2024", "Taxes/2025", "Projects/garden"):
        for number in range(rng.randint(3, 12)):
            real(root / "Documents" / folder / f"document-{number:02d}.pdf",
                 rng.randint(20, 400) * 1024, rng)
    sparse(root / "#recycle" / "old-export.zip", 3 * GIB)
    sparse(root / ".snapshot" / "daily" / "Media.img", 50 * GIB)
    # The same backup twice through a hard link: counted once.
    original = root / "Backups" / "laptop" / "week-01.tar.zst"
    os.link(original, root / "Backups" / "laptop-latest.tar.zst")
    (root / "Media" / "Latest movie").symlink_to(Path("Movies") / MOVIES[-1])
    (root / "Documents" / "Empty folder").mkdir()


def mutate(root: Path, step: int) -> None:
    """Change set `step`: something grows, something is cleaned up, something new appears."""
    rng = random.Random(1000 + step)
    for show in SERIES:
        season = root / "Media" / "Series" / show / f"Season {SERIES[show] + step}"
        for episode in range(1, rng.randint(4, 8) + 1):
            sparse(season / f"{show} S{SERIES[show] + step:02d}E{episode:02d}.mkv",
                   rng.randint(400, 1300) * MIB)
    backups = sorted((root / "Backups" / MACHINES[step % len(MACHINES)]).glob("week-*"))
    for old in backups[: max(1, len(backups) // 2)]:
        old.unlink()
    year = 2025 + step
    for month in range(1, 4):
        for number in range(rng.randint(10, 30)):
            sparse(root / "Photos" / str(year) / f"{year}-{month:02d}" / f"IMG_{number:04d}.jpg",
                   rng.randint(2, 9) * MIB)
    recycle = root / "#recycle" / "old-export.zip"
    if recycle.exists():
        recycle.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--mutate", type=int, default=0, help="apply change set N (1, 2…)")
    args = parser.parse_args()
    if args.mutate:
        mutate(args.root, args.mutate)
    else:
        create(args.root, args.seed)


if __name__ == "__main__":
    main()
