"""The derived per-project index (metadata.json) and its in-memory cache.

Each data point's meta.json is the source of truth. metadata.json holds one flat row
per data point (slug, name, updated_at and the metadata fields) so that listing and
search never have to touch individual data point directories. It can always be rebuilt
from disk.
"""

import bisect
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .fsutil import atomic_write_text, project_lock, read_json

log = logging.getLogger(__name__)

INDEX_FILE = "metadata.json"
META_FILE = "meta.json"
INDEX_VERSION = 1


def entry_from_record(record: dict) -> dict:
    """Flatten a meta.json record into an index row."""
    row = {"slug": record["slug"], "name": record["name"], "updated_at": record.get("updated_at")}
    row.update({k: v for k, v in (record.get("meta") or {}).items() if v is not None})
    return row


def iter_data_point_dirs(project_dir: Path):
    with os.scandir(project_dir) as it:
        for e in it:
            if e.is_dir(follow_symlinks=False) and not e.name.startswith("."):
                if os.path.exists(os.path.join(e.path, META_FILE)):
                    yield Path(e.path)


def _slug_key(row: dict) -> str:
    return row["slug"]


@dataclass
class Index:
    items: list[dict] = field(default_factory=list)  # sorted by slug
    by_slug: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def from_items(cls, items: list[dict]) -> "Index":
        items = sorted(items, key=_slug_key)
        return cls(items=items, by_slug={r["slug"]: r for r in items})

    def copy(self) -> "Index":
        return Index(items=list(self.items), by_slug=dict(self.by_slug))

    def upsert(self, row: dict) -> None:
        slug = row["slug"]
        if slug in self.by_slug:
            pos = bisect.bisect_left(self.items, slug, key=_slug_key)
            self.items[pos] = row
        else:
            bisect.insort(self.items, row, key=_slug_key)
        self.by_slug[slug] = row

    def remove(self, slug: str) -> None:
        if slug not in self.by_slug:
            return
        pos = bisect.bisect_left(self.items, slug, key=_slug_key)
        del self.items[pos]
        del self.by_slug[slug]


def _serialize(index: Index) -> str:
    # One row per line: compact for 100k rows yet still greppable and diff-friendly.
    rows = ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in index.items)
    head = f'{{"version":{INDEX_VERSION},"count":{len(index.items)},"items":[\n'
    return head + rows + ("\n" if rows else "") + "]}\n"


def scan_project(project_dir: Path) -> Index:
    rows = []
    for dp in iter_data_point_dirs(project_dir):
        try:
            rows.append(entry_from_record(read_json(dp / META_FILE)))
        except (OSError, ValueError, KeyError) as exc:
            log.warning("Skipping unreadable data point %s: %s", dp, exc)
    return Index.from_items(rows)


class IndexCache:
    """Process-wide cache of parsed indexes, invalidated by metadata.json's stat signature."""

    def __init__(self) -> None:
        self._entries: dict[Path, tuple[tuple, Index]] = {}
        self._mutex = threading.Lock()

    @staticmethod
    def _signature(path: Path) -> tuple | None:
        try:
            st = path.stat()
        except FileNotFoundError:
            return None
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    def _load_from_disk(self, project_dir: Path) -> Index | None:
        path = project_dir / INDEX_FILE
        sig = self._signature(path)
        if sig is None:
            return None
        with self._mutex:
            cached = self._entries.get(project_dir)
            if cached and cached[0] == sig:
                return cached[1]
        try:
            data = read_json(path)
            index = Index.from_items(data["items"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("Index %s unreadable (%s); will rebuild", path, exc)
            return None
        with self._mutex:
            self._entries[project_dir] = (sig, index)
        return index

    def _store(self, project_dir: Path, index: Index) -> None:
        path = project_dir / INDEX_FILE
        atomic_write_text(path, _serialize(index))
        with self._mutex:
            self._entries[project_dir] = (self._signature(path), index)

    def get(self, project_dir: Path) -> Index:
        """Return the project's index, rebuilding it if missing or corrupt. Must not be
        called while holding the project lock (use the *_locked methods instead)."""
        index = self._load_from_disk(project_dir)
        if index is None:
            index = self.rebuild(project_dir)
        return index

    def rebuild(self, project_dir: Path, *, locked: bool = False) -> Index:
        if not locked:
            with project_lock(project_dir):
                return self.rebuild(project_dir, locked=True)
        index = scan_project(project_dir)
        self._store(project_dir, index)
        return index

    def _get_locked(self, project_dir: Path) -> Index:
        index = self._load_from_disk(project_dir)
        return index if index is not None else self.rebuild(project_dir, locked=True)

    # Writes are copy-on-write so concurrent readers holding the old Index never see a
    # half-mutated list, and a failed write leaves the cache untouched.
    def upsert_locked(self, project_dir: Path, row: dict) -> None:
        index = self._get_locked(project_dir).copy()
        index.upsert(row)
        self._store(project_dir, index)

    def remove_locked(self, project_dir: Path, slug: str) -> None:
        index = self._get_locked(project_dir).copy()
        index.remove(slug)
        self._store(project_dir, index)

    def invalidate(self, project_dir: Path) -> None:
        with self._mutex:
            self._entries.pop(project_dir, None)
