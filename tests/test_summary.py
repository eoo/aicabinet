import pytest

from prode.errors import ValidationError
from prode.summary import (
    EMPTY_BODY, FILES_END, FILES_START, parse_summary, rebuild_summary, render_summary,
    validate_section_content,
)

SECTIONS = ["Background", "Notes"]


def test_render_and_parse():
    text = render_summary("Tarun", SECTIONS, {"Background": "Engineer.\n\n### Detail\nmore"},
                          {"cv.pdf": {"description": "Resume"}})
    assert text.startswith("# Tarun\n")
    assert FILES_START in text and "- `cv.pdf`: Resume" in text and FILES_END in text
    p = parse_summary(text)
    assert p.title == "Tarun"
    assert list(p.sections) == ["Background", "Notes", "Files"]
    assert p.sections["Background"] == "Engineer.\n\n### Detail\nmore"
    assert p.sections["Notes"] == EMPTY_BODY


def test_rebuild_preserves_and_updates():
    text = render_summary("Tarun", SECTIONS, {"Background": "old bg", "Notes": "keep"}, None)
    text = text.replace("## Files", "## Hand Added\nmine\n\n## Files")
    new = rebuild_summary(text, "Tarun S", SECTIONS + ["Links"], {"Background": "new bg"},
                          {"a.txt": {"description": "A"}})
    p = parse_summary(new)
    assert p.title == "Tarun S"
    assert p.sections["Background"] == "new bg"
    assert p.sections["Notes"] == "keep"
    assert p.sections["Links"] == EMPTY_BODY
    assert p.sections["Hand Added"] == "mine"
    assert "- `a.txt`: A" in p.sections["Files"]
    assert list(p.sections)[-1] == "Files"


def test_validate_section_content():
    assert validate_section_content(SECTIONS, {"background": "x"}) == {"Background": "x"}
    with pytest.raises(ValidationError, match="Unknown summary section"):
        validate_section_content(SECTIONS, {"Other": "x"})
    with pytest.raises(ValidationError, match="heading"):
        validate_section_content(SECTIONS, {"Notes": "## sneaky"})
