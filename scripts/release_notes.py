"""Print the "What's new" section of a release: the English text of the notes added since a tag.

Usage: python scripts/release_notes.py PREVIOUS_TAG [REF]

Prints nothing when no note was added.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

NOTES_DIR = "frontend/src/whatsNew/notes"
KINDS = (("new", "New"), ("improved", "Improved"), ("fixed", "Fixed"))


def added_notes(previous: str, ref: str) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=A", previous, ref, "--", NOTES_DIR],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return sorted(line for line in out.splitlines() if line.endswith(".json"))


def render(notes: list[dict[str, object]]) -> str:
    if not notes:
        return ""
    lines = ["## What's new", ""]
    for kind, heading in KINDS:
        group = [note for note in notes if note.get("kind") == kind]
        if not group:
            continue
        lines += [f"### {heading}", ""]
        for note in group:
            en = note["en"]
            assert isinstance(en, dict)
            lines.append(f"- **{en['title']}**: {en['body']}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        return 2
    previous = argv[1]
    ref = argv[2] if len(argv) == 3 else "HEAD"
    notes = [json.loads(Path(path).read_text(encoding="utf-8")) for path in added_notes(previous, ref)]
    sys.stdout.write(render(notes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
