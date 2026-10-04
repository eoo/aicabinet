import pytest

from prode.errors import ValidationError
from prode.index import Index
from prode.query import resolve, search
from prode.schema import Schema

from conftest import INTERVIEW_FIELDS, SECTIONS

ROWS = [
    {"slug": "tarun-singh", "name": "Tarun Singh", "updated_at": "2026-10-01", "fullname": "Tarun Singh",
     "status": "interview", "experience": 5, "tags": ["Python", "go"], "applied_on": "2026-09-01", "remote": True},
    {"slug": "rahul-verma", "name": "Rahul Verma", "updated_at": "2026-10-02", "fullname": "Rahul Verma",
     "status": "applied", "experience": 2, "tags": ["java"], "applied_on": "2026-09-15"},
    {"slug": "anita-rao", "name": "Anita Rao", "updated_at": "2026-10-03", "fullname": "Anita Rao",
     "status": "offer", "experience": 8.5, "remote": False},
]
SCHEMA = Schema.from_dict({"fields": INTERVIEW_FIELDS, "summary_sections": SECTIONS})


def slugs(filters=None, **kw):
    return [r["slug"] for r in search(ROWS, SCHEMA, filters=filters, limit=200, **kw)["results"]]


@pytest.mark.parametrize("filters,expected", [
    ({"status": "interview"}, ["tarun-singh"]),
    ({"status": ["applied", "offer"]}, ["rahul-verma", "anita-rao"]),
    ({"status": {"ne": "offer"}}, ["tarun-singh", "rahul-verma"]),
    ({"status": {"nin": ["offer"]}}, ["tarun-singh", "rahul-verma"]),
    ({"experience": {"gte": 5}}, ["tarun-singh", "anita-rao"]),
    ({"experience": {"gt": 2, "lt": 8}}, ["tarun-singh"]),
    ({"experience": {"lte": "2"}}, ["rahul-verma"]),
    ({"tags": "python"}, ["tarun-singh"]),
    ({"tags": {"in": ["java", "go"]}}, ["tarun-singh", "rahul-verma"]),
    ({"tags": {"nin": ["java"]}}, ["tarun-singh", "anita-rao"]),
    ({"tags": {"exists": False}}, ["anita-rao"]),
    ({"applied_on": {"gte": "2026-09-10"}}, ["rahul-verma"]),
    ({"remote": True}, ["tarun-singh"]),
    ({"remote": {"exists": True}}, ["tarun-singh", "anita-rao"]),
    ({"fullname": {"contains": "RAO"}}, ["anita-rao"]),
    ({"name": "tarun singh"}, ["tarun-singh"]),
    ({"status": "interview", "experience": {"gte": 6}}, []),
])
def test_filters(filters, expected):
    assert sorted(slugs(filters)) == sorted(expected)


def test_keyword():
    assert slugs(keyword="JAVA") == ["rahul-verma"]
    assert slugs(keyword="rao") == ["anita-rao"]
    assert slugs(keyword="offer") == ["anita-rao"]


def test_sort_and_paginate():
    assert slugs(sort_by="-experience") == ["anita-rao", "tarun-singh", "rahul-verma"]
    assert slugs(sort_by="applied_on") == ["tarun-singh", "rahul-verma", "anita-rao"]  # missing last
    page = search(ROWS, SCHEMA, sort_by="experience", limit=1, offset=1)
    assert page["total"] == 3 and page["has_more"] and [r["slug"] for r in page["results"]] == ["tarun-singh"]


def test_fields_projection():
    r = search(ROWS, SCHEMA, fields=["status"], limit=1)["results"][0]
    assert set(r) == {"slug", "name", "status"}


@pytest.mark.parametrize("kwargs,msg", [
    ({"filters": {"nope": 1}}, "Unknown filter field"),
    ({"filters": {"status": {"gt": "a"}}}, "not valid"),
    ({"filters": {"status": "hired"}}, "not one of"),
    ({"filters": {"experience": {"in": 3}}}, "needs a list"),
    ({"sort_by": "tags"}, "Cannot sort"),
    ({"fields": ["zzz"]}, "Unknown field"),
])
def test_errors(kwargs, msg):
    with pytest.raises(ValidationError, match=msg):
        search(ROWS, SCHEMA, **kwargs)


def test_resolve():
    assert resolve(ROWS, "Tarun Singh")[0] == {"slug": "tarun-singh", "name": "Tarun Singh", "score": 100.0}
    assert resolve(ROWS, "tarn sing")[0]["slug"] == "tarun-singh"
    assert resolve(ROWS, "rahool")[0]["slug"] == "rahul-verma"
    assert resolve(ROWS, "anita")[0]["slug"] == "anita-rao"
    assert resolve(ROWS, "zzzzqqq") == []


def test_index_upsert_remove():
    idx = Index.from_items([dict(r) for r in ROWS])
    assert [r["slug"] for r in idx.items] == ["anita-rao", "rahul-verma", "tarun-singh"]
    idx.upsert({"slug": "bob", "name": "Bob"})
    idx.upsert({"slug": "anita-rao", "name": "Anita R"})
    assert [r["slug"] for r in idx.items] == ["anita-rao", "bob", "rahul-verma", "tarun-singh"]
    assert idx.by_slug["anita-rao"]["name"] == "Anita R"
    idx.remove("bob")
    idx.remove("missing")
    assert [r["slug"] for r in idx.items] == ["anita-rao", "rahul-verma", "tarun-singh"]
