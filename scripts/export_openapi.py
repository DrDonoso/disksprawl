"""Exports the API contract (frontend/src/api/openapi.json) without a database or a server.

    python scripts/export_openapi.py           write the file
    python scripts/export_openapi.py --check   exit 1 when it is out of date
"""

import argparse
import json
from pathlib import Path

from disksprawl.app import create_app

OUTPUT = Path(__file__).resolve().parents[1] / "frontend" / "src" / "api" / "openapi.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = (
        json.dumps(create_app().openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != rendered:
            raise SystemExit("The API contract is stale: run python scripts/export_openapi.py")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(rendered, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
