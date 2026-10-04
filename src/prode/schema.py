"""Project schema: metadata field definitions and the SUMMARY.md section layout."""

import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .fsutil import atomic_write_json, read_json

SCHEMA_FILE = "schema.json"
FIELD_TYPES = ("string", "number", "integer", "boolean", "date", "enum", "list")
RESERVED_FIELDS = frozenset({"slug", "name", "created_at", "updated_at"})
RESERVED_SECTIONS = frozenset({"files"})
_FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass
class FieldSpec:
    type: str
    required: bool = False
    values: list[str] | None = None  # enum only
    description: str | None = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"type": self.type}
        if self.required:
            d["required"] = True
        if self.values is not None:
            d["values"] = list(self.values)
        if self.description:
            d["description"] = self.description
        return d


@dataclass
class Schema:
    fields: dict[str, FieldSpec]
    summary_sections: list[str]
    version: int = 1
    name: str = ""  # project display name
    created_at: str | None = None

    # ---- construction / persistence -------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict) -> "Schema":
        if not isinstance(data, dict):
            raise ValidationError("Schema must be a JSON object.")
        raw_fields = data.get("fields") or {}
        if not isinstance(raw_fields, dict):
            raise ValidationError("schema.fields must be an object mapping field name -> spec.")
        fields = {name: _parse_field(name, spec) for name, spec in raw_fields.items()}
        sections = _parse_sections(data.get("summary_sections") or [])
        return cls(
            fields=fields,
            summary_sections=sections,
            version=int(data.get("version", 1)),
            name=str(data.get("name") or ""),
            created_at=data.get("created_at"),
        )

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "name": self.name,
            "created_at": self.created_at,
            "fields": {name: spec.to_dict() for name, spec in self.fields.items()},
            "summary_sections": list(self.summary_sections),
        }

    @classmethod
    def load(cls, project_dir: Path) -> "Schema":
        return cls.from_dict(read_json(project_dir / SCHEMA_FILE))

    def save(self, project_dir: Path) -> None:
        atomic_write_json(project_dir / SCHEMA_FILE, self.to_dict())

    # ---- validation -------------------------------------------------------------------

    def validate_meta(self, meta: dict | None, *, partial: bool = False) -> dict:
        """Validate and coerce a metadata dict.

        With partial=True (a patch), required fields may be absent and a value of None
        means "remove this field".
        """
        meta = meta or {}
        if not isinstance(meta, dict):
            raise ValidationError("meta must be an object.")
        unknown = sorted(set(meta) - set(self.fields))
        if unknown:
            raise ValidationError(
                f"Unknown metadata field(s): {unknown}. Valid fields: {sorted(self.fields)}."
            )
        out: dict[str, Any] = {}
        for name, value in meta.items():
            spec = self.fields[name]
            if value is None:
                if spec.required:
                    raise ValidationError(f"Field {name!r} is required and cannot be null.")
                if partial:
                    out[name] = None
                continue
            out[name] = coerce(spec, value, name)
        if not partial:
            missing = [n for n, s in self.fields.items() if s.required and n not in out]
            if missing:
                raise ValidationError(f"Missing required metadata field(s): {missing}.")
        return out

    # ---- non-breaking evolution -------------------------------------------------------

    def extend(
        self,
        add_fields: dict | None = None,
        add_enum_values: dict[str, list[str]] | None = None,
        add_summary_sections: list[str] | None = None,
    ) -> "Schema":
        fields = dict(self.fields)
        for name, raw in (add_fields or {}).items():
            if name in fields:
                raise ValidationError(f"Field {name!r} already exists.")
            spec = _parse_field(name, raw)
            if spec.required:
                raise ValidationError(
                    f"New field {name!r} cannot be required: existing data points would become invalid."
                )
            fields[name] = spec
        for name, values in (add_enum_values or {}).items():
            spec = fields.get(name)
            if spec is None or spec.type != "enum":
                raise ValidationError(f"{name!r} is not an enum field.")
            merged = list(spec.values or [])
            merged += [v for v in values if v not in merged]
            fields[name] = FieldSpec(spec.type, spec.required, merged, spec.description)
        sections = _parse_sections(list(self.summary_sections) + list(add_summary_sections or []))
        return Schema(
            fields=fields,
            summary_sections=sections,
            version=self.version,
            name=self.name,
            created_at=self.created_at,
        )


def coerce(spec: FieldSpec, value: Any, name: str = "value") -> Any:
    """Coerce value to the field's type, raising ValidationError when impossible."""
    t = spec.type
    try:
        if t == "string":
            if isinstance(value, (dict, list)):
                raise TypeError
            return str(value)
        if t == "number":
            if isinstance(value, bool):
                raise TypeError
            num = float(value)
            if not math.isfinite(num):
                raise ValueError
            return int(num) if num.is_integer() and not isinstance(value, float) else num
        if t == "integer":
            if isinstance(value, bool):
                raise TypeError
            num = float(value)
            if not num.is_integer():
                raise ValueError
            return int(num)
        if t == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.strip().lower() in ("true", "false", "yes", "no", "1", "0"):
                return value.strip().lower() in ("true", "yes", "1")
            if value in (0, 1):
                return bool(value)
            raise ValueError
        if t == "date":
            if isinstance(value, date):
                return value.isoformat()[:10]
            return date.fromisoformat(str(value).strip()[:10]).isoformat()
        if t == "enum":
            sval = str(value)
            if sval not in (spec.values or []):
                raise ValidationError(f"Field {name!r}: {sval!r} is not one of {spec.values}.")
            return sval
        if t == "list":
            items = [value] if isinstance(value, str) else list(value)
            if any(isinstance(i, (dict, list)) for i in items):
                raise TypeError
            return [str(i) for i in items]
    except ValidationError:
        raise
    except (TypeError, ValueError):
        pass
    raise ValidationError(f"Field {name!r}: cannot interpret {value!r} as {t}.")


def _parse_field(name: str, raw: Any) -> FieldSpec:
    if not _FIELD_NAME_RE.match(name):
        raise ValidationError(
            f"Invalid field name {name!r}: use lowercase snake_case starting with a letter."
        )
    if name in RESERVED_FIELDS:
        raise ValidationError(f"Field name {name!r} is reserved ({sorted(RESERVED_FIELDS)}).")
    if isinstance(raw, str):
        raw = {"type": raw}
    if not isinstance(raw, dict):
        raise ValidationError(f"Field {name!r}: spec must be an object or a type name.")
    ftype = raw.get("type")
    if ftype not in FIELD_TYPES:
        raise ValidationError(f"Field {name!r}: type must be one of {list(FIELD_TYPES)}, got {ftype!r}.")
    values = raw.get("values")
    if ftype == "enum":
        if not values or not isinstance(values, list):
            raise ValidationError(f"Enum field {name!r} needs a non-empty 'values' list.")
        values = [str(v) for v in values]
        if len(set(values)) != len(values):
            raise ValidationError(f"Enum field {name!r} has duplicate values.")
    elif values is not None:
        raise ValidationError(f"Field {name!r}: 'values' is only valid for enum fields.")
    return FieldSpec(
        type=ftype,
        required=bool(raw.get("required", False)),
        values=values,
        description=raw.get("description"),
    )


def _parse_sections(sections: Any) -> list[str]:
    if not isinstance(sections, list):
        raise ValidationError("summary_sections must be a list of section titles.")
    out: list[str] = []
    seen: set[str] = set()
    for s in sections:
        title = str(s).strip()
        if not title or "\n" in title or title.startswith("#"):
            raise ValidationError(f"Invalid summary section title {s!r}.")
        if title.lower() in RESERVED_SECTIONS:
            raise ValidationError(f"Section {title!r} is reserved and managed automatically.")
        if title.lower() in seen:
            raise ValidationError(f"Duplicate summary section {title!r}.")
        seen.add(title.lower())
        out.append(title)
    return out
