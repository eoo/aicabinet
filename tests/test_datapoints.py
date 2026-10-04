import json
import subprocess

import pytest

from prode import datapoints as dp
from prode import projects as pj
from prode.errors import AlreadyExistsError, NotFoundError, ValidationError
from prode.index import INDEX_FILE
from prode.store import Store


def git_log(store):
    out = subprocess.run(["git", "-C", str(store.root), "log", "--format=%s"], capture_output=True, text=True)
    return out.stdout.splitlines()


def add_tarun(store, project, **kw):
    return dp.add_data_point(
        store, project, "Tarun Singh",
        {"fullname": "Tarun Singh", "status": "interview", "experience": 5, "tags": ["python"]},
        {"Background": "Backend engineer."}, **kw,
    )


def test_create_and_list_projects(store, project):
    pdir = store.root / project
    for f in ["OVERVIEW.md", "schema.json", "SUMMARY_TEMPLATE.md", INDEX_FILE]:
        assert (pdir / f).exists()
    assert (pdir / "OVERVIEW.md").read_text().startswith("# Interviewer Management\n")
    assert pj.list_projects(store) == [{"project": project, "name": "Interviewer Management", "data_points": 0}]
    loaded = pj.load_project(store, project)
    assert loaded["schema"]["summary_sections"] == ["Background", "Assessment", "Notes"]
    assert "gte" in loaded["filter_operators"]["experience"]
    with pytest.raises(AlreadyExistsError):
        pj.create_project(store, "Interviewer Management", "dup")
    with pytest.raises(NotFoundError, match="Existing projects"):
        pj.load_project(store, "nope")
    assert git_log(store)[0] == f"create_project {project}"


def test_failed_create_leaves_nothing(store):
    with pytest.raises(ValidationError):
        pj.create_project(store, "Bad", "x", {"slug": "string"})
    assert not (store.root / "bad").exists()


def test_add_get_update_delete(store, project):
    res = add_tarun(store, project)
    assert res["slug"] == "tarun-singh"
    assert res["empty_summary_sections"] == ["Assessment", "Notes"]
    got = dp.get_data_point(store, project, "tarun-singh")
    assert got["meta"]["status"] == "interview"
    assert "## Background\nBackend engineer." in got["summary"]
    assert "## Files" in got["summary"]

    # duplicate names get a suffixed slug and a warning
    res2 = add_tarun(store, project)
    assert res2["slug"] == "tarun-singh-2" and "share the name" in res2["warning"]
    with pytest.raises(AlreadyExistsError):
        add_tarun(store, project, slug="tarun-singh")

    upd = dp.update_data_point(store, project, "tarun-singh", name="Tarun S.",
                               meta={"status": "offer", "tags": None}, summary={"Notes": "Strong."})
    assert upd["meta"] == {"fullname": "Tarun Singh", "status": "offer", "experience": 5}
    summary = dp.get_data_point(store, project, "tarun-singh")["summary"]
    assert summary.startswith("# Tarun S.\n") and "Backend engineer." in summary and "Strong." in summary
    with pytest.raises(ValidationError, match="required"):
        dp.update_data_point(store, project, "tarun-singh", meta={"fullname": None})

    r = dp.search(store, project, filters={"status": "offer"})
    assert [x["slug"] for x in r["results"]] == ["tarun-singh"]

    dp.delete_data_point(store, project, "tarun-singh")
    assert (store.root / project / ".archive" / "tarun-singh" / "meta.json").exists()
    assert dp.list_all_data(store, project)["total"] == 1
    with pytest.raises(NotFoundError, match="Did you mean: tarun-singh-2"):
        dp.get_data_point(store, project, "tarun-singh")

    log = git_log(store)
    assert log[0] == f"delete_data_point {project}/tarun-singh"
    assert f"update_data_point {project}/tarun-singh" in log


def test_validation_on_add(store, project):
    with pytest.raises(ValidationError, match="Missing required"):
        dp.add_data_point(store, project, "X", {"status": "applied"})
    with pytest.raises(ValidationError, match="Unknown summary section"):
        dp.add_data_point(store, project, "X", {"fullname": "X", "status": "applied"}, {"Bogus": "x"})
    assert dp.list_all_data(store, project)["total"] == 0
    assert not (store.root / project / "x").exists()


def test_files(store, project, tmp_path):
    add_tarun(store, project)
    src = tmp_path / "resume.txt"
    src.write_text("Tarun's resume\n")
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4\x00\x01binary")
    dp.add_file(store, project, "tarun-singh", str(src), "Plain-text resume")
    dp.add_file(store, project, "tarun-singh", str(pdf), "PDF CV")
    summary = dp.get_data_point(store, project, "tarun-singh")["summary"]
    assert "- `resume.txt`: Plain-text resume" in summary and "- `cv.pdf`: PDF CV" in summary
    assert dp.read_file(store, project, "tarun-singh", "resume.txt")["content"] == "Tarun's resume\n"
    assert dp.read_file(store, project, "tarun-singh", "resume.txt", max_chars=5)["truncated"]
    binary = dp.read_file(store, project, "tarun-singh", "cv.pdf")
    assert binary["binary"] and binary["path"].endswith("cv.pdf")
    with pytest.raises(AlreadyExistsError):
        dp.add_file(store, project, "tarun-singh", str(src), "again")
    for bad in ["meta.json", "../x", "SUMMARY.md"]:
        with pytest.raises(ValidationError):
            dp.add_file(store, project, "tarun-singh", str(src), "x", filename=bad)

    (store.root / project / "tarun-singh" / "manual.txt").write_text("dropped in by hand")
    listing = {f["filename"]: f for f in dp.list_files(store, project, "tarun-singh")["files"]}
    assert set(listing) == {"resume.txt", "cv.pdf", "manual.txt"} and not listing["manual.txt"]["tracked"]

    dp.remove_file(store, project, "tarun-singh", "resume.txt")
    summary = dp.get_data_point(store, project, "tarun-singh")["summary"]
    assert "resume.txt" not in summary and "cv.pdf" in summary
    assert not (store.root / project / "tarun-singh" / "resume.txt").exists()


def test_index_rebuild_and_cache(store, project):
    add_tarun(store, project)
    pdir = store.root / project
    (pdir / INDEX_FILE).unlink()
    # a fresh Store (no cache) rebuilds the missing index transparently
    fresh = Store(store.root)
    assert dp.list_all_data(fresh, project)["total"] == 1
    # external edit to meta.json is picked up by reindex
    meta_path = pdir / "tarun-singh" / "meta.json"
    rec = json.loads(meta_path.read_text())
    rec["meta"]["status"] = "rejected"
    meta_path.write_text(json.dumps(rec))
    assert dp.reindex(fresh, project) == {"project": project, "data_points": 1}
    assert dp.search(fresh, project, filters={"status": "rejected"})["total"] == 1
    # corrupt index is rebuilt too
    (pdir / INDEX_FILE).write_text("{not json")
    assert dp.search(Store(store.root), project, filters={"status": "rejected"})["total"] == 1


def test_schema_evolution(store, project):
    add_tarun(store, project)
    res = pj.update_schema(store, project, add_fields={"city": "string"}, add_summary_sections=["Links"])
    assert "note" in res
    dp.update_data_point(store, project, "tarun-singh", meta={"city": "Pune"}, summary={"Links": "x"})
    got = dp.get_data_point(store, project, "tarun-singh")
    assert got["meta"]["city"] == "Pune" and "## Links\nx" in got["summary"]
    assert "## Links" in (store.root / project / "SUMMARY_TEMPLATE.md").read_text()


def test_resolve_data_point(store, project):
    add_tarun(store, project)
    assert dp.resolve_data_point(store, project, "tarun sigh")["matches"][0]["slug"] == "tarun-singh"
