"""Deterministic search over a project index: filters, keyword, sort, pagination, fuzzy resolve.

All of this runs in the server process over the cached index, so the LLM only ever sees
the (small, paginated) result rows.
"""

from collections.abc import Callable
from typing import Any

from rapidfuzz import fuzz, process
from rapidfuzz.utils import default_process

from .errors import ValidationError
from .schema import FieldSpec, Schema, coerce

DEFAULT_LIMIT = 20
MAX_LIMIT = 200

# Built-in columns every index row has, queryable like schema fields.
BUILTIN_FIELDS: dict[str, FieldSpec] = {
    "slug": FieldSpec("string"),
    "name": FieldSpec("string"),
    "updated_at": FieldSpec("string"),
}

OPS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "string": ("eq", "ne", "in", "nin", "contains", "gt", "gte", "lt", "lte", "exists"),
    "number": ("eq", "ne", "in", "nin", "gt", "gte", "lt", "lte", "exists"),
    "integer": ("eq", "ne", "in", "nin", "gt", "gte", "lt", "lte", "exists"),
    "date": ("eq", "ne", "in", "nin", "gt", "gte", "lt", "lte", "exists"),
    "boolean": ("eq", "ne", "exists"),
    "enum": ("eq", "ne", "in", "nin", "exists"),
    "list": ("contains", "in", "nin", "exists"),
}
_KEYWORD_TYPES = ("string", "enum", "list")

Predicate = Callable[[dict], bool]
_MISSING = object()


def queryable_fields(schema: Schema) -> dict[str, FieldSpec]:
    return {**BUILTIN_FIELDS, **schema.fields}


def _norm(v: Any) -> Any:
    return v.casefold() if isinstance(v, str) else v


def _compile_one(name: str, spec: FieldSpec, op: str, raw: Any) -> Predicate:
    allowed = OPS_BY_TYPE[spec.type]
    if op not in allowed:
        raise ValidationError(f"Operator {op!r} not valid for {spec.type} field {name!r}. Allowed: {list(allowed)}.")

    if op == "exists":
        want = bool(raw)
        return lambda r: (r.get(name) is not None) == want

    # Values for list fields are individual elements, so coerce them as strings.
    elem_spec = FieldSpec("string") if spec.type == "list" else spec
    if op in ("in", "nin"):
        if not isinstance(raw, list):
            raise ValidationError(f"Operator {op!r} on {name!r} needs a list of values.")
        values = {_norm(coerce(elem_spec, v, name)) for v in raw}
        if spec.type == "list":
            hit = lambda r: any(_norm(x) in values for x in (r.get(name) or ()))  # noqa: E731
        else:
            hit = lambda r: r.get(name) is not None and _norm(r.get(name)) in values  # noqa: E731
        return hit if op == "in" else (lambda r: not hit(r))

    value = _norm(coerce(elem_spec, raw, name))
    if op == "contains":
        if spec.type == "list":
            return lambda r: any(_norm(x) == value for x in (r.get(name) or ()))
        return lambda r: isinstance(r.get(name), str) and value in r[name].casefold()
    if op == "eq":
        return lambda r: _norm(r.get(name, _MISSING)) == value
    if op == "ne":
        return lambda r: _norm(r.get(name, _MISSING)) != value

    cmp = {
        "gt": lambda a, b: a > b,
        "gte": lambda a, b: a >= b,
        "lt": lambda a, b: a < b,
        "lte": lambda a, b: a <= b,
    }[op]

    def pred(r: dict) -> bool:
        v = r.get(name)
        return v is not None and cmp(_norm(v), value)

    return pred


def compile_filters(schema: Schema, filters: dict | None) -> list[Predicate]:
    if not filters:
        return []
    if not isinstance(filters, dict):
        raise ValidationError("filters must be an object: {field: value} or {field: {op: value}}.")
    fields = queryable_fields(schema)
    preds: list[Predicate] = []
    for name, cond in filters.items():
        spec = fields.get(name)
        if spec is None:
            raise ValidationError(f"Unknown filter field {name!r}. Queryable fields: {sorted(fields)}.")
        if isinstance(cond, dict):
            if not cond:
                raise ValidationError(f"Empty condition for {name!r}.")
            for op, raw in cond.items():
                preds.append(_compile_one(name, spec, op, raw))
        elif isinstance(cond, list) and spec.type != "list":
            preds.append(_compile_one(name, spec, "in", cond))
        else:
            preds.append(_compile_one(name, spec, "contains" if spec.type == "list" else "eq", cond))
    return preds


def _keyword_predicate(schema: Schema, keyword: str) -> Predicate:
    needle = keyword.casefold().strip()
    cols = ["slug", "name"] + [n for n, s in schema.fields.items() if s.type in _KEYWORD_TYPES]

    def pred(r: dict) -> bool:
        for c in cols:
            v = r.get(c)
            if v is None:
                continue
            if isinstance(v, list):
                if any(needle in str(x).casefold() for x in v):
                    return True
            elif needle in str(v).casefold():
                return True
        return False

    return pred


def _sort(rows: list[dict], schema: Schema, sort_by: str | None) -> list[dict]:
    if not sort_by:
        return rows  # index order (by slug)
    desc = sort_by.startswith("-")
    key = sort_by.lstrip("-+")
    fields = queryable_fields(schema)
    if key not in fields or fields[key].type == "list":
        sortable = sorted(n for n, s in fields.items() if s.type != "list")
        raise ValidationError(f"Cannot sort by {key!r}. Sortable fields: {sortable}.")
    present = [r for r in rows if r.get(key) is not None]
    missing = [r for r in rows if r.get(key) is None]
    present.sort(key=lambda r: _norm(r[key]), reverse=desc)
    return present + missing  # rows lacking the field always go last


def _project(row: dict, fields: list[str] | None) -> dict:
    if not fields:
        return row
    keep = {"slug", "name", *fields}
    return {k: v for k, v in row.items() if k in keep}


def clamp_limit(limit: int | None) -> int:
    return max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))


def search(
    rows: list[dict],
    schema: Schema,
    *,
    keyword: str | None = None,
    filters: dict | None = None,
    sort_by: str | None = None,
    limit: int | None = DEFAULT_LIMIT,
    offset: int = 0,
    fields: list[str] | None = None,
) -> dict:
    preds = compile_filters(schema, filters)
    if keyword and keyword.strip():
        preds.append(_keyword_predicate(schema, keyword))
    if fields:
        unknown = sorted(set(fields) - set(queryable_fields(schema)))
        if unknown:
            raise ValidationError(f"Unknown field(s) in 'fields': {unknown}.")
    matched = [r for r in rows if all(p(r) for p in preds)] if preds else list(rows)
    matched = _sort(matched, schema, sort_by)
    limit = clamp_limit(limit)
    offset = max(0, int(offset or 0))
    page = matched[offset : offset + limit]
    return {
        "total": len(matched),
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < len(matched),
        "results": [_project(r, fields) for r in page],
    }


def resolve(rows: list[dict], query: str, limit: int = 5, score_cutoff: float = 50.0) -> list[dict]:
    """Map a (possibly misspelled or partial) name to candidate data points, best first."""
    q = (query or "").strip()
    if not q:
        return []
    qf = q.casefold()
    exact = [r for r in rows if r["slug"] == qf or r["name"].casefold() == qf]
    if exact:
        return [{"slug": r["slug"], "name": r["name"], "score": 100.0} for r in exact[:limit]]

    best: dict[int, float] = {}
    for choices, needle in (
        ([r["name"] for r in rows], q),
        ([r["slug"].replace("-", " ") for r in rows], q),
    ):
        for _, score, idx in process.extract(
            needle, choices, scorer=fuzz.WRatio, processor=default_process,
            limit=limit * 2, score_cutoff=score_cutoff,
        ):
            best[idx] = max(score, best.get(idx, 0.0))
    ranked = sorted(best.items(), key=lambda kv: (-kv[1], rows[kv[0]]["slug"]))[:limit]
    return [{"slug": rows[i]["slug"], "name": rows[i]["name"], "score": round(s, 1)} for i, s in ranked]
