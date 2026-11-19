"""A disposable DiskSprawl for browser tests: a fresh database and a synthetic sample tree.

    python scripts/smoke_server.py --port 7889

It serves the built frontend (frontend/dist) on 127.0.0.1 only, with the sample tree from
make_sample_tree.py as the scan root, recreated at every start in a temporary folder. Schedules never
fire on their own, so the tests do not depend on the time of day they run at, and notifications are
never sent: a stand-in answers for the push services. Never deploy it.
"""

import argparse
import logging
import shutil
import sys
import tempfile
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import make_sample_tree  # noqa: E402

from disksprawl import webpush  # noqa: E402
from disksprawl.app import create_app  # noqa: E402
from disksprawl.config import Settings  # noqa: E402
from disksprawl.scheduler import Scheduler  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=7889)
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="folder for the database and the sample tree (default: a temporary folder by port)",
    )
    args = parser.parse_args()
    base = args.root or Path(tempfile.gettempdir()) / f"disksprawl-e2e-{args.port}"
    shutil.rmtree(base, ignore_errors=True)
    scan_root = base / "data"
    make_sample_tree.create(scan_root / "storage", seed=7)
    settings = Settings(
        data_dir=base / "state",
        scan_root=scan_root,
        frontend_dir=ROOT / "frontend" / "dist",
        _env_file=None,  # type: ignore[call-arg]
    )
    logging.basicConfig(level=logging.WARNING)
    Scheduler.paused = True
    # Notifications never leave this machine: the browser tests register stand-in devices, and
    # what they are told goes to this stand-in for the push services.
    webpush.TRANSPORT = lambda request: webpush.Response(201)
    print(f"Sample tree: {scan_root / 'storage'}", flush=True)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
