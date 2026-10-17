"""Trim cached SerpApi responses (data/raw, gitignored) into small evaluation fixtures (evals/fixtures, committed).

Only the fields the extractors read are kept: no API keys, thumbnails, tokens or full pages.

Run:  uv run python evals/build_fixtures.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW, OUT = ROOT / "data" / "raw", ROOT / "evals" / "fixtures"
SUBURBS = ["parow", "durbanville", "observatory"]
ORGANIC_KEYS = ("link", "title", "snippet", "source", "date")
PLACE_KEYS = ("title", "type", "address")
POLICE_KEYS = ("title", "type", "address", "phone")  # police stations are public institutions


def trim_organic(response: dict) -> dict:
    out = {"organic_results": [{k: r[k] for k in ORGANIC_KEYS if k in r} for r in response.get("organic_results", [])]}
    local = response.get("local_results")
    places = local.get("places", []) if isinstance(local, dict) else (local or [])
    if places:
        out["local_results"] = {"places": [
            {**{k: p[k] for k in PLACE_KEYS if k in p},
             "links": {"website": (p.get("links") or {}).get("website") or p.get("website")}}
            for p in places
        ]}  # fmt: skip
    return out


def trim_places(response: dict) -> dict:
    out = {}
    if response.get("place_results"):
        out["place_results"] = {k: response["place_results"][k] for k in POLICE_KEYS if k in response["place_results"]}
    if isinstance(response.get("local_results"), list):
        out["local_results"] = [{k: p[k] for k in POLICE_KEYS if k in p} for p in response["local_results"]]
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for suburb in SUBURBS:
        for name, trim in [("listings", trim_organic), ("safety-views", trim_organic),
                           ("safety-patrols", trim_organic), ("police", trim_places)]:  # fmt: skip
            src = RAW / f"{name}-{suburb}.json"
            record = json.loads(src.read_text())
            fixture = {"fetched_at": record["fetched_at"], "params": {"q": record["params"].get("q")},
                       "response": trim(record["response"])}  # fmt: skip
            (OUT / src.name).write_text(json.dumps(fixture, indent=1, ensure_ascii=False) + "\n")
    print(f"Wrote {len(list(OUT.glob('*.json')))} fixtures to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
