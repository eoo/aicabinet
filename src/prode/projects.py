"""Project-level operations."""

import re
import shutil
from pathlib import Path

from .errors import AlreadyExistsError, ValidationError
from .fsutil import atomic_write_text, now_iso, project_lock, safe_join
from .index import INDEX_FILE
from .query import OPS_BY_TYPE, queryable_fields
from .schema import Schema
from .slug import slugify, validate_slug
from .store import Store
from .summary import render_template

OVERVIEW_FILE = "OVERVIEW.md"
TEMPLATE_FILE = "SUMMARY_TEMPLATE.md"
DEFAULT_SECTIONS = ["Summary", "Notes"]
_COUNT_RE = re.compile(rb'"count":(\d+)')


def _overview_text(name: str, overview: str) -> str:
    text = (overview or "").strip()
    if not text.startswith("# "):
        text = f"# {name}\n\n{text}"
    return text + "\n"


def _count(store: Store, project_dir: Path) -> int:
    # Cheap: the count sits in the first line of metadata.json; avoid parsing 100k rows.
    try:
        with open(project_dir / INDEX_FILE, "rb") as f:
            m = _COUNT_RE.search(f.read(128))
        if m:
            return int(m.group(1))
    except OSError:
        pass
    return len(store.cache.get(project_dir).items)


def create_project(
    store: Store,
    name: str,
    overview: str,
    fields: dict | None = None,
    summary_sections: list[str] | None = None,
    slug: str | None = None,
) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValidationError("Project name is required.")
    slug = validate_slug(slug) if slug else slugify(name)
    schema = Schema.from_dict(
        {
            "name": name,
            "created_at": now_iso(),
            "fields": fields or {},
            "summary_sections": summary_sections or DEFAULT_SECTIONS,
        }
    )
    project_dir = safe_join(store.root, slug)
    if project_dir.exists():
        raise AlreadyExistsError(f"Project directory {slug!r} already exists.")
    project_dir.mkdir()
    try:
        with project_lock(project_dir):
            schema.save(project_dir)
            atomic_write_text(project_dir / OVERVIEW_FILE, _overview_text(name, overview))
            atomic_write_text(project_dir / TEMPLATE_FILE, render_template(schema.summary_sections))
            store.cache.rebuild(project_dir, locked=True)
    except BaseException:
        shutil.rmtree(project_dir, ignore_errors=True)
        raise
    store.commit(f"create_project {slug}", [project_dir])
    return {"project": slug, "name": name, "path": str(project_dir), "schema": schema.to_dict()}


def list_projects(store: Store) -> list[dict]:
    out = []
    for slug in store.project_slugs():
        pdir = store.root / slug
        try:
            schema = store.schema(pdir)
            name = schema.name or slug
        except Exception:  # a broken project should not hide the others
            name = slug
        out.append({"project": slug, "name": name, "data_points": _count(store, pdir)})
    return out


def load_project(store: Store, project: str) -> dict:
    pdir = store.project_dir(project)
    schema = store.schema(pdir)
    fields = queryable_fields(schema)
    return {
        "project": project,
        "name": schema.name or project,
        "path": str(pdir),
        "overview": (pdir / OVERVIEW_FILE).read_text(encoding="utf-8"),
        "schema": schema.to_dict(),
        "summary_template": (pdir / TEMPLATE_FILE).read_text(encoding="utf-8"),
        "data_points": _count(store, pdir),
        "filter_operators": {n: list(OPS_BY_TYPE[s.type]) for n, s in fields.items()},
    }


def update_project_overview(store: Store, project: str, overview: str) -> dict:
    pdir = store.project_dir(project)
    schema = store.schema(pdir)
    with project_lock(pdir):
        atomic_write_text(pdir / OVERVIEW_FILE, _overview_text(schema.name or project, overview))
    store.commit(f"update_project_overview {project}", [pdir / OVERVIEW_FILE])
    return {"project": project, "updated": OVERVIEW_FILE}


def update_schema(
    store: Store,
    project: str,
    add_fields: dict | None = None,
    add_enum_values: dict[str, list[str]] | None = None,
    add_summary_sections: list[str] | None = None,
) -> dict:
    pdir = store.project_dir(project)
    with project_lock(pdir):
        schema = store.schema(pdir).extend(add_fields, add_enum_values, add_summary_sections)
        schema.save(pdir)
        atomic_write_text(pdir / TEMPLATE_FILE, render_template(schema.summary_sections))
    store.commit(f"update_schema {project}", [pdir])
    result = {"project": project, "schema": schema.to_dict()}
    if add_summary_sections:
        result["note"] = (
            "Existing SUMMARY.md files gain the new sections the next time each data point is updated."
        )
    return result
