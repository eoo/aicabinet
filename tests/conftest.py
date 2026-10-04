import pytest

from prode import projects as pj
from prode.store import Store

INTERVIEW_FIELDS = {
    "fullname": {"type": "string", "required": True},
    "status": {"type": "enum", "values": ["applied", "screening", "interview", "offer", "rejected"], "required": True},
    "experience": {"type": "number"},
    "tags": {"type": "list"},
    "applied_on": {"type": "date"},
    "remote": {"type": "boolean"},
}
SECTIONS = ["Background", "Assessment", "Notes"]


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "root")


@pytest.fixture
def project(store):
    pj.create_project(
        store, "Interviewer Management", "Tracks candidates.", INTERVIEW_FIELDS, SECTIONS
    )
    return "interviewer-management"
