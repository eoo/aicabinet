"""Slug generation for project and data point directory names."""

import re
import unicodedata
from collections.abc import Container

from .errors import ValidationError

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_LEN = 80


def slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = _NON_ALNUM.sub("-", normalized.lower()).strip("-")[:MAX_LEN].rstrip("-")
    if not slug:
        raise ValidationError(f"Cannot derive a slug from {text!r}; pass an explicit slug.")
    return slug


def validate_slug(slug: str) -> str:
    if not SLUG_RE.match(slug) or len(slug) > MAX_LEN:
        raise ValidationError(
            f"Invalid slug {slug!r}: use lowercase letters, digits and single hyphens (max {MAX_LEN} chars)."
        )
    return slug


def unique_slug(base: str, taken: Container[str]) -> str:
    """Return base, or base-2, base-3, ... whichever is free."""
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"
