"""Entry point: `python -m prode --root PATH` (stdio MCP server)."""

import argparse
import logging
import sys

from .config import ENV_VAR, resolve_root
from .server import build_server
from .store import Store


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="prode", description="prode MCP server (stdio)")
    parser.add_argument("--root", help=f"Data root directory (default: ${ENV_VAR} or ~/prode-data)")
    parser.add_argument("--no-git", action="store_true", help="Do not version the data root with git")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    # stdout carries the MCP protocol, so logs must go to stderr.
    logging.basicConfig(stream=sys.stderr, level=args.log_level.upper(), format="%(levelname)s %(name)s: %(message)s")
    store = Store(resolve_root(args.root), use_git=not args.no_git)
    logging.getLogger("prode").info("Serving data root %s", store.root)
    build_server(store).run("stdio")


if __name__ == "__main__":
    main()
