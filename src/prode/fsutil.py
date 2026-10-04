"""Filesystem helpers: atomic writes, project locks, safe path joins."""

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .errors import ValidationError

LOCK_FILE = ".lock"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def atomic_write_text(path: Path, content: str) -> None:
    """Write to a temp file in the same directory, fsync, then rename over the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, data: Any, *, indent: int | None = 2) -> None:
    atomic_write_text(path, json.dumps(data, indent=indent, ensure_ascii=False) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@contextmanager
def project_lock(project_dir: Path) -> Iterator[None]:
    """Exclusive advisory lock serialising writes within one project."""
    with open(project_dir / LOCK_FILE, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def safe_join(base: Path, *parts: str) -> Path:
    """Join untrusted path components onto base, refusing anything that escapes it."""
    for part in parts:
        if not part or part in (".", "..") or "/" in part or "\\" in part or "\x00" in part:
            raise ValidationError(f"Invalid path component: {part!r}")
    target = base.joinpath(*parts).resolve()
    if not target.is_relative_to(base.resolve()):
        raise ValidationError(f"Path escapes its base directory: {'/'.join(parts)!r}")
    return target
