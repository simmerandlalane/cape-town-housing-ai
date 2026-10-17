"""SerpApi access with an on-disk cache and a per-run budget.

Cache records look like {"fetched_at", "params", "response"} so every fact can be traced back to
the exact query and time. Cached results cost nothing; a live search counts against the budget.
"""

import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import serpapi

DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
# Bundled, read-only results for the example suburbs, so the app runs with no API key (see demo/build_demo.py).
DEMO_CACHE_DIR = Path(__file__).resolve().parents[2] / "demo" / "cache"
TIMEOUT_S = 30  # first attempt; each retry waits longer
RETRIES = 2
RETRY_DELAY_S = 2


class SearchBudgetExceeded(RuntimeError):
    pass


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class CachedSearch:
    def __init__(
        self,
        api_key: str | None = None,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        max_live_searches: int = 40,
        demo_dir: Path | None = DEMO_CACHE_DIR,
    ):
        self.api_key = api_key or os.getenv("SERPAPI_API_KEY") or os.getenv("SERPAPI_KEY")
        self.cache_dir = cache_dir
        self.demo_dir = demo_dir
        self.max_live_searches = max_live_searches
        self.live_searches = 0
        self.demo_hits = 0  # results served from the bundled demo data
        self._client: serpapi.Client | None = None

    def path_for(self, name: str) -> Path:
        return self.cache_dir / f"{name}.json"

    def _cached_path(self, name: str) -> Path | None:
        """Your own cache first, then the bundled demo data."""
        for directory in (self.cache_dir, self.demo_dir):
            if directory is not None and (directory / f"{name}.json").exists():
                return directory / f"{name}.json"
        return None

    def is_cached(self, name: str) -> bool:
        return self._cached_path(name) is not None

    def search(self, name: str, params: dict) -> dict:
        """Return the cached record for `name`, searching live (once) if it isn't cached."""
        if cached := self._cached_path(name):
            if cached.parent == self.demo_dir:
                self.demo_hits += 1
            return json.loads(cached.read_text())

        if self.live_searches >= self.max_live_searches:
            raise SearchBudgetExceeded(f"Live search budget of {self.max_live_searches} used up this run.")
        if not self.api_key:
            raise RuntimeError("SERPAPI_API_KEY is not set (see .env.example).")
        if self._client is None:
            self._client = serpapi.Client(api_key=self.api_key)

        # Pass a copy: serpapi.Client writes api_key into the dict it receives, and we save `params`.
        # Every attempt counts against the budget: a timed-out request may still be billed by SerpApi.
        response = None
        for attempt in range(1, RETRIES + 2):
            self.live_searches += 1
            try:
                response = self._client.search(dict(params), timeout=TIMEOUT_S * attempt).as_dict()
                break
            except (serpapi.TimeoutError, serpapi.HTTPConnectionError):
                if attempt > RETRIES or self.live_searches >= self.max_live_searches:
                    raise
                time.sleep(RETRY_DELAY_S * attempt)
        record = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "params": params,
            "response": response,
        }
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.path_for(name).write_text(json.dumps(record, indent=2, ensure_ascii=False))
        return record
