"""Shared handle on the data root: path resolution, index cache, git."""

import logging
from pathlib import Path

from . import gitops
from .errors import NotFoundError
from .fsutil import safe_join
from .index import IndexCache
from .schema import SCHEMA_FILE, Schema
from .slug import validate_slug

log = logging.getLogger(__name__)


class Store:
    def __init__(self, root: Path, *, use_git: bool = True):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.cache = IndexCache()
        self.git = gitops.ensure_repo(self.root) if use_git else False

    def project_slugs(self) -> list[str]:
        return sorted(
            p.name for p in self.root.iterdir()
            if p.is_dir() and not p.name.startswith(".") and (p / SCHEMA_FILE).exists()
        )

    def project_dir(self, project: str) -> Path:
        validate_slug(project)
        path = safe_join(self.root, project)
        if not (path / SCHEMA_FILE).exists():
            raise NotFoundError(
                f"Project {project!r} not found. Existing projects: {self.project_slugs()}."
            )
        return path

    def schema(self, project_dir: Path) -> Schema:
        return Schema.load(project_dir)

    def commit(self, message: str, paths: list[Path]) -> None:
        if self.git:
            gitops.commit(self.root, message, paths)
