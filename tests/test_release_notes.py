import release_notes


def note(kind: str, title: str, body: str) -> dict[str, object]:
    return {"kind": kind, "en": {"title": title, "body": body}, "es": {"title": "t", "body": "b"}}


def test_no_notes_prints_nothing() -> None:
    assert release_notes.render([]) == ""


def test_notes_are_grouped_by_kind() -> None:
    text = release_notes.render(
        [note("fixed", "A fix", "Fixed."), note("new", "A feature", "Added."), note("new", "Another", "Also.")]
    )
    assert text.index("### New") < text.index("### Fixed")
    assert "### Improved" not in text
    assert "- **A feature**: Added." in text
    assert text.startswith("## What's new")
