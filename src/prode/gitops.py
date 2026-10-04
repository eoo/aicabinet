"""Version the data root with git. Every failure here is logged, never raised: git is an
audit trail, not part of the write path's correctness."""

import logging
import shutil
import subprocess
from pathlib import Path

from .fsutil import atomic_write_text

log = logging.getLogger(__name__)

GITIGNORE = """\
# Managed by prode
**/.lock
**/metadata.json
**/.*.tmp
"""
# Used only when the user has no git identity configured.
_FALLBACK_IDENTITY = ["-c", "user.name=prode", "-c", "user.email=prode@localhost"]


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=check, timeout=60
    )


def _identity_args(root: Path) -> list[str]:
    have_name = _git(root, "config", "user.name", check=False).stdout.strip()
    have_email = _git(root, "config", "user.email", check=False).stdout.strip()
    return [] if have_name and have_email else _FALLBACK_IDENTITY


def available() -> bool:
    return shutil.which("git") is not None


def ensure_repo(root: Path) -> bool:
    """git init the root (with a .gitignore) if it is not already a repo."""
    if not available():
        log.warning("git not found; data root will not be versioned")
        return False
    try:
        if not (root / ".git").exists():
            _git(root, "init", "--quiet")
            log.info("Initialised git repository in %s", root)
        gi = root / ".gitignore"
        if not gi.exists():
            atomic_write_text(gi, GITIGNORE)
            commit(root, "prode: initialise data root", [".gitignore"])
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("git init failed in %s: %s", root, exc)
        return False


def commit(root: Path, message: str, paths: list[str | Path]) -> bool:
    """Stage the given paths (including deletions) and commit. Returns True if a commit was made."""
    if not available() or not (root / ".git").exists():
        return False
    rel = [str(Path(p).relative_to(root)) if Path(p).is_absolute() else str(p) for p in paths]
    try:
        _git(root, "add", "--all", "--", *rel)
        if _git(root, "diff", "--cached", "--quiet", check=False).returncode == 0:
            return False  # nothing staged
        _git(root, *_identity_args(root), "commit", "--quiet", "--no-verify", "-m", message)
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", "") or exc
        log.warning("git commit failed (%s): %s", message, detail)
        return False
