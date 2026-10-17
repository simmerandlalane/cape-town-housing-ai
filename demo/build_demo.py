"""Copy the cached searches for the demo suburbs into demo/cache, stripped of anything the app doesn't need.

The demo lets anyone run the app with no SerpApi key and zero searches. Run after refreshing data/raw:

    uv run python demo/build_demo.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW, OUT = ROOT / "data" / "raw", ROOT / "demo" / "cache"
SUBURBS = ["parow", "durbanville", "observatory"]
DESTINATION = "bellville"
NAMES = ["listings-{s}", "commute-{s}-{d}-transit", "commute-{s}-{d}-driving", "news-{s}", "safety-views-{s}",
         "safety-patrols-{s}", "police-{s}", "amenity-{s}-grocery-store", "amenity-{s}-gym", "amenity-{s}-park",
         "amenity-{s}-pharmacy"]  # fmt: skip
DROP_KEYS = {"pagination", "serpapi_pagination", "ai_overview", "inline_images", "thumbnail", "thumbnails",
             "serpapi_link", "serpapi_thumbnail", "api_key", "about_page_link", "about_page_serpapi_link",
             "about_this_result", "redirect_link"}  # fmt: skip


def strip(value):
    """Remove page tokens, pagination links and thumbnails, recursively."""
    if isinstance(value, dict):
        return {k: strip(v) for k, v in value.items() if k not in DROP_KEYS and not k.endswith("_token")}
    if isinstance(value, list):
        return [strip(v) for v in value]
    return value


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for suburb in SUBURBS:
        for template in NAMES:
            name = template.format(s=suburb, d=DESTINATION)
            record = json.loads((RAW / f"{name}.json").read_text())
            (OUT / f"{name}.json").write_text(json.dumps(strip(record), indent=1, ensure_ascii=False) + "\n")
    print(f"Wrote {len(list(OUT.glob('*.json')))} demo files to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
