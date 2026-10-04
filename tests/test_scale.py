"""100k data point scale check. Run with: pytest -m slow"""

import json
import random
import time

import pytest

from prode import datapoints as dp
from prode.index import META_FILE
from prode.store import Store

pytestmark = pytest.mark.slow
N = 100_000


def test_100k_search(store, project):
    pdir = store.root / project
    rnd = random.Random(0)
    statuses = ["applied", "screening", "interview", "offer", "rejected"]
    for i in range(N):
        d = pdir / f"person-{i:06d}"
        d.mkdir()
        rec = {"slug": d.name, "name": f"Person {i}", "created_at": "2026-01-01", "updated_at": "2026-01-01",
               "meta": {"fullname": f"Person {i}", "status": rnd.choice(statuses),
                        "experience": rnd.randint(0, 20), "tags": rnd.sample(["py", "go", "js", "rs"], 2)},
               "files": {}}
        (d / META_FILE).write_text(json.dumps(rec))

    t = time.perf_counter()
    assert dp.reindex(store, project)["data_points"] == N
    print(f"\nreindex: {time.perf_counter() - t:.2f}s")

    cold = Store(store.root, use_git=False)
    t = time.perf_counter()
    dp.search(cold, project, filters={"status": "offer"}, limit=1)
    print(f"cold search (parse index): {time.perf_counter() - t:.2f}s")

    t = time.perf_counter()
    res = dp.search(cold, project, filters={"status": "offer", "experience": {"gte": 10}, "tags": "py"},
                    sort_by="-experience", limit=20)
    warm = time.perf_counter() - t
    print(f"warm filtered search: {warm * 1000:.0f}ms, total={res['total']}")
    assert res["total"] > 0 and len(res["results"]) == 20
    assert warm < 0.5

    t = time.perf_counter()
    dp.search(cold, project, keyword="person 4242")
    print(f"warm keyword search: {(time.perf_counter() - t) * 1000:.0f}ms")

    t = time.perf_counter()
    m = dp.resolve_data_point(cold, project, "persn 4242")
    print(f"resolve: {(time.perf_counter() - t) * 1000:.0f}ms -> {m['matches'][:1]}")

    t = time.perf_counter()
    dp.add_data_point(cold, project, "New Person", {"fullname": "New", "status": "applied"})
    print(f"add_data_point (index rewrite): {(time.perf_counter() - t) * 1000:.0f}ms")
