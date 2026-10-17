"""Shared helpers. Snippets in the tests are copied from real SerpApi results (Oct 2026)."""

import pytest


@pytest.fixture
def make_search_record():
    def _make(*results: dict, fetched_at: str = "2026-10-17T20:26:33+00:00") -> dict:
        return {
            "fetched_at": fetched_at,
            "params": {"engine": "google", "q": "flat to rent in Parow Cape Town"},
            "response": {"search_metadata": {"id": "test-search-id"}, "organic_results": list(results)},
        }

    return _make
