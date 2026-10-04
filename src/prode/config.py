"""Resolve the data root directory."""

import os
from pathlib import Path

ENV_VAR = "PRODE_ROOT"
DEFAULT_ROOT = Path.home() / "prode-data"


def resolve_root(cli_value: str | None = None) -> Path:
    """CLI flag wins, then $PRODE_ROOT, then ~/prode-data."""
    raw = cli_value or os.environ.get(ENV_VAR)
    root = Path(raw).expanduser() if raw else DEFAULT_ROOT
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root
