"""Render and parse SUMMARY.md files.

Layout (sections come from the project schema):

    # <name>

    ## <section 1>
    <body>

    ## Files
    <!-- prode:files:start -->
    - `resume.pdf`: Latest resume
    <!-- prode:files:end -->

SUMMARY.md is always regenerated from (name, section bodies, files), so the structure
stays identical across every data point in a project. Section bodies edited by hand are
preserved because updates parse the existing file first.
"""

import re
from dataclasses import dataclass, field

from .errors import ValidationError

FILES_HEADING = "Files"
FILES_START = "<!-- prode:files:start -->"
FILES_END = "<!-- prode:files:end -->"
EMPTY_BODY = "_Not provided yet._"
_HEADING_RE = re.compile(r"^(#{1,2})\s+(.*?)\s*$")


@dataclass
class ParsedSummary:
    title: str = ""
    preamble: str = ""
    sections: dict[str, str] = field(default_factory=dict)  # insertion-ordered


def render_summary(
    name: str,
    section_titles: list[str],
    content: dict[str, str] | None,
    files: dict[str, dict] | None,
    *,
    preamble: str = "",
    extra_sections: dict[str, str] | None = None,
) -> str:
    content = content or {}
    parts = [f"# {name}", ""]
    if preamble.strip():
        parts += [preamble.strip(), ""]
    for title in section_titles:
        body = (content.get(title) or "").strip() or EMPTY_BODY
        parts += [f"## {title}", body, ""]
    for title, body in (extra_sections or {}).items():
        parts += [f"## {title}", body.strip() or EMPTY_BODY, ""]
    parts += [f"## {FILES_HEADING}", render_files_block(files), ""]
    return "\n".join(parts)


def render_files_block(files: dict[str, dict] | None) -> str:
    lines = [FILES_START]
    if files:
        for fname in sorted(files):
            desc = (files[fname] or {}).get("description") or ""
            lines.append(f"- `{fname}`" + (f": {desc}" if desc else ""))
    else:
        lines.append("_No files attached._")
    lines.append(FILES_END)
    return "\n".join(lines)


def render_template(section_titles: list[str]) -> str:
    parts = ["# <Data point name>", ""]
    for title in section_titles:
        parts += [f"## {title}", f"<!-- {title} content -->", ""]
    parts += [
        f"## {FILES_HEADING}",
        FILES_START,
        "<!-- Managed automatically: one line per attached file with its description. -->",
        FILES_END,
        "",
    ]
    return "\n".join(parts)


def parse_summary(text: str) -> ParsedSummary:
    parsed = ParsedSummary()
    current: str | None = None
    buf: list[str] = []
    preamble: list[str] = []

    def flush() -> None:
        if current is not None:
            parsed.sections[current] = "\n".join(buf).strip()

    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m and m.group(1) == "#" and not parsed.title and current is None:
            parsed.title = m.group(2)
            continue
        if m and m.group(1) == "##":
            flush()
            current, buf = m.group(2), []
            continue
        (buf if current is not None else preamble).append(line)
    flush()
    parsed.preamble = "\n".join(preamble).strip()
    return parsed


def validate_section_content(section_titles: list[str], content: dict[str, str] | None) -> dict[str, str]:
    """Check that only schema sections are supplied and that bodies won't break the layout."""
    content = content or {}
    if not isinstance(content, dict):
        raise ValidationError("summary must be an object mapping section title -> markdown text.")
    by_lower = {t.lower(): t for t in section_titles}
    out: dict[str, str] = {}
    for key, body in content.items():
        title = by_lower.get(str(key).strip().lower())
        if title is None:
            raise ValidationError(f"Unknown summary section {key!r}. Sections: {section_titles}.")
        body = "" if body is None else str(body)
        for line in body.splitlines():
            m = _HEADING_RE.match(line)
            if m:
                raise ValidationError(
                    f"Section {title!r} contains a level-1/2 heading ({line.strip()!r}); use ### or deeper."
                )
        out[title] = body
    return out


def rebuild_summary(
    existing_text: str | None,
    name: str,
    section_titles: list[str],
    updates: dict[str, str] | None,
    files: dict[str, dict] | None,
) -> str:
    """Re-render SUMMARY.md keeping existing section bodies, applying updates, refreshing Files."""
    parsed = parse_summary(existing_text) if existing_text else ParsedSummary()
    known = {t.lower() for t in section_titles} | {FILES_HEADING.lower()}
    current = {}
    for title, body in parsed.sections.items():
        match = next((t for t in section_titles if t.lower() == title.lower()), None)
        if match:
            current[match] = "" if body == EMPTY_BODY else body
    extras = {t: b for t, b in parsed.sections.items() if t.lower() not in known}
    current.update(updates or {})
    return render_summary(
        name, section_titles, current, files, preamble=parsed.preamble, extra_sections=extras
    )
