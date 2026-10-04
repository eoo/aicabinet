import pytest

from prode.errors import ValidationError
from prode.fsutil import atomic_write_json, read_json, safe_join
from prode.slug import slugify, unique_slug, validate_slug


def test_slugify():
    assert slugify("Tarun Singh") == "tarun-singh"
    assert slugify("  José  Núñez!! ") == "jose-nunez"
    assert slugify("a/../b") == "a-b"
    with pytest.raises(ValidationError):
        slugify("!!!")


def test_unique_slug():
    assert unique_slug("tarun", set()) == "tarun"
    assert unique_slug("tarun", {"tarun", "tarun-2"}) == "tarun-3"


def test_validate_slug():
    assert validate_slug("ok-slug-1") == "ok-slug-1"
    for bad in ["Bad", "a--b", "-a", "a/b", "..", ""]:
        with pytest.raises(ValidationError):
            validate_slug(bad)


def test_atomic_write_roundtrip(tmp_path):
    p = tmp_path / "x.json"
    atomic_write_json(p, {"a": 1})
    assert read_json(p) == {"a": 1}
    assert [f.name for f in tmp_path.iterdir()] == ["x.json"]  # no temp files left


def test_safe_join(tmp_path):
    assert safe_join(tmp_path, "a", "b.txt") == (tmp_path / "a" / "b.txt").resolve()
    for bad in ["..", "a/b", "", "."]:
        with pytest.raises(ValidationError):
            safe_join(tmp_path, bad)
