import json
from pathlib import Path

import pytest

from ct_housing.search import CachedSearch, SearchBudgetExceeded


class FakeResults:
    def __init__(self, data):
        self.data = data

    def as_dict(self):
        return dict(self.data)


class FakeClient:
    """Mimics serpapi.Client, which writes api_key into the params dict it is given."""

    def __init__(self, fail_times=0):
        self.calls = 0
        self.fail_times = fail_times

    def search(self, params, timeout=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            import serpapi

            raise serpapi.TimeoutError("slow")
        params["api_key"] = "SECRET-KEY"
        return FakeResults({"search_metadata": {"id": "abc"}})


@pytest.fixture
def searcher(tmp_path):
    s = CachedSearch(api_key="SECRET-KEY", cache_dir=tmp_path, max_live_searches=2, demo_dir=None)
    s._client = FakeClient()
    return s


def test_api_key_is_never_written_to_cache(searcher, tmp_path):
    params = {"engine": "google", "q": "flat to rent in Parow"}
    searcher.search("q1", params)
    assert "SECRET-KEY" not in (tmp_path / "q1.json").read_text()
    assert "api_key" not in params  # caller's dict untouched


def test_cache_hit_costs_nothing(searcher):
    searcher.search("q1", {"q": "a"})
    record = searcher.search("q1", {"q": "a"})
    assert searcher._client.calls == 1
    assert searcher.live_searches == 1
    assert record["response"]["search_metadata"]["id"] == "abc"


def test_budget_stops_live_searches(searcher):
    searcher.search("q1", {"q": "a"})
    searcher.search("q2", {"q": "b"})
    with pytest.raises(SearchBudgetExceeded):
        searcher.search("q3", {"q": "c"})


def test_record_keeps_provenance(searcher, tmp_path):
    searcher.search("q1", {"engine": "google", "q": "a"})
    record = json.loads((tmp_path / "q1.json").read_text())
    assert record["params"] == {"engine": "google", "q": "a"}
    assert "fetched_at" in record


def test_timeouts_are_retried(tmp_path, monkeypatch):
    monkeypatch.setattr("ct_housing.search.RETRY_DELAY_S", 0)
    s = CachedSearch(api_key="k", cache_dir=tmp_path, demo_dir=None)
    s._client = FakeClient(fail_times=2)
    s.search("q1", {"q": "a"})
    assert s._client.calls == 3 and s.live_searches == 3  # every attempt may be billed


def test_gives_up_after_retries(tmp_path, monkeypatch):
    import serpapi

    monkeypatch.setattr("ct_housing.search.RETRY_DELAY_S", 0)
    s = CachedSearch(api_key="k", cache_dir=tmp_path, demo_dir=None)
    s._client = FakeClient(fail_times=5)
    with pytest.raises(serpapi.TimeoutError):
        s.search("q1", {"q": "a"})
    assert not (tmp_path / "q1.json").exists()


def test_demo_data_is_used_when_not_in_own_cache(tmp_path):
    own, demo = tmp_path / "own", tmp_path / "demo"
    demo.mkdir()
    (demo / "q1.json").write_text(json.dumps({"fetched_at": "x", "params": {}, "response": {"from": "demo"}}))
    s = CachedSearch(api_key=None, cache_dir=own, demo_dir=demo)  # no key needed for demo results
    assert s.is_cached("q1") and not s.is_cached("q2")
    assert s.search("q1", {})["response"] == {"from": "demo"}
    assert s.demo_hits == 1 and s.live_searches == 0


def test_bundled_demo_covers_the_example_suburbs():
    from ct_housing.models import UserPreferences
    from ct_housing.research import new_searches_needed

    s = CachedSearch(api_key=None, cache_dir=Path("/nonexistent"))
    prefs = UserPreferences(destination="Bellville")
    assert new_searches_needed(s, ["Parow", "Durbanville", "Observatory"], prefs) == 0
    assert new_searches_needed(s, ["Parow"], prefs.model_copy(update={"has_car": True})) == 0
