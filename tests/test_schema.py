import pytest

from prode.errors import ValidationError
from prode.schema import Schema

from conftest import INTERVIEW_FIELDS, SECTIONS


@pytest.fixture
def schema():
    return Schema.from_dict({"fields": INTERVIEW_FIELDS, "summary_sections": SECTIONS})


def test_roundtrip(schema):
    assert Schema.from_dict(schema.to_dict()).to_dict() == schema.to_dict()


def test_validate_and_coerce(schema):
    out = schema.validate_meta(
        {"fullname": "T", "status": "applied", "experience": "3.5", "tags": "python",
         "applied_on": "2026-01-05T10:00:00", "remote": "yes"}
    )
    assert out == {"fullname": "T", "status": "applied", "experience": 3.5, "tags": ["python"],
                   "applied_on": "2026-01-05", "remote": True}


@pytest.mark.parametrize("meta,msg", [
    ({"status": "applied"}, "Missing required"),
    ({"fullname": "T", "status": "hired"}, "not one of"),
    ({"fullname": "T", "status": "applied", "bogus": 1}, "Unknown metadata"),
    ({"fullname": "T", "status": "applied", "experience": "lots"}, "cannot interpret"),
    ({"fullname": "T", "status": "applied", "applied_on": "yesterday"}, "cannot interpret"),
    ({"fullname": "T", "status": "applied", "remote": "maybe"}, "cannot interpret"),
])
def test_validation_errors(schema, meta, msg):
    with pytest.raises(ValidationError, match=msg):
        schema.validate_meta(meta)


def test_partial(schema):
    assert schema.validate_meta({"experience": None}, partial=True) == {"experience": None}
    with pytest.raises(ValidationError, match="required"):
        schema.validate_meta({"status": None}, partial=True)


@pytest.mark.parametrize("fields", [
    {"Name": "string"}, {"slug": "string"}, {"x": {"type": "weird"}},
    {"x": {"type": "enum"}}, {"x": {"type": "string", "values": ["a"]}},
])
def test_bad_field_specs(fields):
    with pytest.raises(ValidationError):
        Schema.from_dict({"fields": fields})


def test_bad_sections():
    for secs in [["A", "a"], ["Files"], ["## X"]]:
        with pytest.raises(ValidationError):
            Schema.from_dict({"summary_sections": secs})


def test_extend(schema):
    s2 = schema.extend({"city": "string"}, {"status": ["hired", "applied"]}, ["Links"])
    assert "city" in s2.fields
    assert s2.fields["status"].values[-1] == "hired" and s2.fields["status"].values.count("applied") == 1
    assert s2.summary_sections[-1] == "Links"
    with pytest.raises(ValidationError, match="cannot be required"):
        schema.extend({"x": {"type": "string", "required": True}})
    with pytest.raises(ValidationError, match="already exists"):
        schema.extend({"fullname": "string"})
    with pytest.raises(ValidationError, match="not an enum"):
        schema.extend(add_enum_values={"fullname": ["x"]})
