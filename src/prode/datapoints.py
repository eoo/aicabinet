"""Data point CRUD and file attachments.

Every write follows the same sequence: take the project lock, write meta.json and
SUMMARY.md atomically, upsert the index row, release the lock, then git commit.
"""

import mimetypes
import shutil
from pathlib import Path

from . import query
from .errors import AlreadyExistsError, NotFoundError, ValidationError
from .fsutil import atomic_write_json, atomic_write_text, now_iso, project_lock, read_json, safe_join
from .index import META_FILE, entry_from_record
from .schema import Schema
from .slug import slugify, unique_slug, validate_slug
from .store import Store
from .summary import rebuild_summary, validate_section_content

SUMMARY_FILE = "SUMMARY.md"
ARCHIVE_DIR = ".archive"
RESERVED_FILENAMES = frozenset({META_FILE, SUMMARY_FILE})
DEFAULT_MAX_CHARS = 20_000
_TEXT_SNIFF_BYTES = 8192


class _TakenSlugs:
    """Container view of slugs already used by a directory in the project."""

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir

    def __contains__(self, slug: object) -> bool:
        return isinstance(slug, str) and (self.project_dir / slug).exists()


# ---- helpers ---------------------------------------------------------------------------


def _not_found(store: Store, pdir: Path, project: str, slug: str) -> NotFoundError:
    suggestions = query.resolve(store.cache.get(pdir).items, slug, limit=3)
    return NotFoundError(f"Data point {slug!r} not found in project {project!r}.", suggestions)


def _locate(store: Store, project: str, slug: str) -> tuple[Path, Path]:
    pdir = store.project_dir(project)
    try:
        validate_slug(slug)
    except ValidationError:
        raise _not_found(store, pdir, project, slug) from None
    dp = safe_join(pdir, slug)
    if not (dp / META_FILE).exists():
        raise _not_found(store, pdir, project, slug)
    return pdir, dp


def _write(store: Store, pdir: Path, dp: Path, record: dict, schema: Schema, summary_updates: dict | None) -> str:
    """Persist record + SUMMARY.md and update the index. Caller must hold the project lock."""
    summary_path = dp / SUMMARY_FILE
    existing = summary_path.read_text(encoding="utf-8") if summary_path.exists() else None
    text = rebuild_summary(existing, record["name"], schema.summary_sections, summary_updates, record["files"])
    atomic_write_json(dp / META_FILE, record)
    atomic_write_text(summary_path, text)
    store.cache.upsert_locked(pdir, entry_from_record(record))
    return text


def _public(record: dict) -> dict:
    return {k: record[k] for k in ("slug", "name", "created_at", "updated_at", "meta")}


def _same_name_warning(store: Store, pdir: Path, name: str, slug: str) -> str | None:
    folded = name.casefold()
    others = [r["slug"] for r in store.cache.get(pdir).items if r["name"].casefold() == folded and r["slug"] != slug]
    if others:
        return f"Other data point(s) share the name {name!r}: {others}. Refer to them by slug."
    return None


# ---- CRUD ------------------------------------------------------------------------------


def add_data_point(
    store: Store,
    project: str,
    name: str,
    meta: dict | None = None,
    summary: dict[str, str] | None = None,
    slug: str | None = None,
) -> dict:
    pdir = store.project_dir(project)
    schema = store.schema(pdir)
    name = (name or "").strip()
    if not name:
        raise ValidationError("Data point name is required.")
    clean_meta = schema.validate_meta(meta)
    sections = validate_section_content(schema.summary_sections, summary)

    with project_lock(pdir):
        if slug:
            validate_slug(slug)
            if (pdir / slug).exists():
                raise AlreadyExistsError(f"Data point slug {slug!r} already exists in {project!r}.")
        else:
            slug = unique_slug(slugify(name), _TakenSlugs(pdir))
        dp = safe_join(pdir, slug)
        ts = now_iso()
        record = {"slug": slug, "name": name, "created_at": ts, "updated_at": ts, "meta": clean_meta, "files": {}}
        dp.mkdir()
        try:
            _write(store, pdir, dp, record, schema, sections)
        except BaseException:
            shutil.rmtree(dp, ignore_errors=True)
            raise
    store.commit(f"add_data_point {project}/{slug}", [dp])

    result = {**_public(record), "path": str(dp)}
    missing = [s for s in schema.summary_sections if not sections.get(s, "").strip()]
    if missing:
        result["empty_summary_sections"] = missing
    warning = _same_name_warning(store, pdir, name, slug)
    if warning:
        result["warning"] = warning
    return result


def get_data_point(store: Store, project: str, slug: str) -> dict:
    _, dp = _locate(store, project, slug)
    record = read_json(dp / META_FILE)
    summary_path = dp / SUMMARY_FILE
    return {
        **_public(record),
        "path": str(dp),
        "summary": summary_path.read_text(encoding="utf-8") if summary_path.exists() else "",
        "files": _file_listing(dp, record),
    }


def update_data_point(
    store: Store,
    project: str,
    slug: str,
    name: str | None = None,
    meta: dict | None = None,
    summary: dict[str, str] | None = None,
) -> dict:
    pdir, dp = _locate(store, project, slug)
    schema = store.schema(pdir)
    patch = schema.validate_meta(meta, partial=True)
    sections = validate_section_content(schema.summary_sections, summary)
    if name is not None and not name.strip():
        raise ValidationError("name cannot be empty.")

    with project_lock(pdir):
        record = read_json(dp / META_FILE)
        merged = {**record.get("meta", {}), **patch}
        merged = {k: v for k, v in merged.items() if v is not None}
        record["meta"] = schema.validate_meta(merged)  # full check: required fields still present
        if name is not None:
            record["name"] = name.strip()
        record.setdefault("files", {})
        record["updated_at"] = now_iso()
        _write(store, pdir, dp, record, schema, sections)
    store.commit(f"update_data_point {project}/{slug}", [dp])
    return {**_public(record), "path": str(dp)}


def delete_data_point(store: Store, project: str, slug: str) -> dict:
    """Soft delete: move the directory under .archive/ and drop it from the index."""
    pdir, dp = _locate(store, project, slug)
    with project_lock(pdir):
        archive = pdir / ARCHIVE_DIR
        archive.mkdir(exist_ok=True)
        target = archive / slug
        if target.exists():
            target = archive / f"{slug}-{now_iso().replace(':', '')}"
        dp.rename(target)
        store.cache.remove_locked(pdir, slug)
    store.commit(f"delete_data_point {project}/{slug}", [dp, target])
    return {"project": project, "slug": slug, "archived_to": str(target)}


def list_all_data(
    store: Store,
    project: str,
    limit: int | None = query.DEFAULT_LIMIT,
    offset: int = 0,
    fields: list[str] | None = None,
    sort_by: str | None = None,
) -> dict:
    pdir = store.project_dir(project)
    return query.search(
        store.cache.get(pdir).items, store.schema(pdir), sort_by=sort_by, limit=limit, offset=offset, fields=fields
    )


def search(
    store: Store,
    project: str,
    keyword: str | None = None,
    filters: dict | None = None,
    sort_by: str | None = None,
    limit: int | None = query.DEFAULT_LIMIT,
    offset: int = 0,
    fields: list[str] | None = None,
) -> dict:
    pdir = store.project_dir(project)
    return query.search(
        store.cache.get(pdir).items,
        store.schema(pdir),
        keyword=keyword,
        filters=filters,
        sort_by=sort_by,
        limit=limit,
        offset=offset,
        fields=fields,
    )


def resolve_data_point(store: Store, project: str, name: str, limit: int = 5) -> dict:
    pdir = store.project_dir(project)
    matches = query.resolve(store.cache.get(pdir).items, name, limit=max(1, min(limit, 20)))
    return {"query": name, "matches": matches}


def reindex(store: Store, project: str) -> dict:
    pdir = store.project_dir(project)
    index = store.cache.rebuild(pdir)
    return {"project": project, "data_points": len(index.items)}


# ---- files -----------------------------------------------------------------------------


def _file_listing(dp: Path, record: dict) -> list[dict]:
    tracked = record.get("files") or {}
    out = []
    for p in sorted(dp.iterdir()):
        if not p.is_file() or p.name in RESERVED_FILENAMES or p.name.startswith("."):
            continue
        info = tracked.get(p.name)
        out.append({
            "filename": p.name,
            "size": p.stat().st_size,
            "description": (info or {}).get("description"),
            "tracked": info is not None,
        })
    return out


def _check_filename(filename: str) -> str:
    if filename in RESERVED_FILENAMES or filename.startswith("."):
        raise ValidationError(f"Filename {filename!r} is reserved or hidden.")
    return filename


def add_file(
    store: Store,
    project: str,
    slug: str,
    source_path: str,
    description: str,
    filename: str | None = None,
    overwrite: bool = False,
) -> dict:
    pdir, dp = _locate(store, project, slug)
    src = Path(source_path).expanduser()
    if not src.is_file():
        raise NotFoundError(f"Source file {source_path!r} does not exist or is not a regular file.")
    filename = _check_filename(filename or src.name)
    dest = safe_join(dp, filename)
    schema = store.schema(pdir)
    with project_lock(pdir):
        if dest.exists() and not overwrite:
            raise AlreadyExistsError(f"{filename!r} already exists in {slug!r}; pass overwrite=true to replace it.")
        shutil.copy2(src, dest)
        record = read_json(dp / META_FILE)
        record.setdefault("files", {})[filename] = {"description": (description or "").strip(), "added_at": now_iso()}
        record["updated_at"] = now_iso()
        _write(store, pdir, dp, record, schema, None)
    store.commit(f"add_file {project}/{slug}/{filename}", [dp])
    return {"project": project, "slug": slug, "filename": filename, "path": str(dest), "size": dest.stat().st_size}


def list_files(store: Store, project: str, slug: str) -> dict:
    _, dp = _locate(store, project, slug)
    return {"project": project, "slug": slug, "files": _file_listing(dp, read_json(dp / META_FILE))}


def read_file(store: Store, project: str, slug: str, filename: str, max_chars: int = DEFAULT_MAX_CHARS) -> dict:
    _, dp = _locate(store, project, slug)
    path = safe_join(dp, filename)
    if not path.is_file():
        names = [f["filename"] for f in _file_listing(dp, read_json(dp / META_FILE))]
        raise NotFoundError(f"File {filename!r} not found in {slug!r}. Files: {names}.")
    size = path.stat().st_size
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        head = f.read(_TEXT_SNIFF_BYTES)
    is_text = b"\x00" not in head
    if is_text:
        try:
            head.decode("utf-8")
        except UnicodeDecodeError as exc:
            # A multibyte char may straddle the sniff boundary; anything earlier means binary.
            is_text = exc.start >= len(head) - 3
    result = {"filename": filename, "path": str(path), "size": size, "mime_type": mime}
    if not is_text:
        return {**result, "binary": True, "note": "Binary file: open it from 'path' with a suitable tool."}
    max_chars = max(1, int(max_chars))
    with open(path, encoding="utf-8", errors="replace") as f:
        content = f.read(max_chars + 1)
    return {**result, "binary": False, "truncated": len(content) > max_chars, "content": content[:max_chars]}


def remove_file(store: Store, project: str, slug: str, filename: str) -> dict:
    pdir, dp = _locate(store, project, slug)
    _check_filename(filename)
    path = safe_join(dp, filename)
    schema = store.schema(pdir)
    with project_lock(pdir):
        record = read_json(dp / META_FILE)
        if not path.is_file() and filename not in record.get("files", {}):
            raise NotFoundError(f"File {filename!r} not found in {slug!r}.")
        path.unlink(missing_ok=True)
        record.setdefault("files", {}).pop(filename, None)
        record["updated_at"] = now_iso()
        _write(store, pdir, dp, record, schema, None)
    store.commit(f"remove_file {project}/{slug}/{filename}", [dp])
    return {"project": project, "slug": slug, "removed": filename}
