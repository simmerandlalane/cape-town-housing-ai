"""Research candidate areas and print a ranked, explained shortlist (no AI yet).

Run:  uv run scripts/rank_areas.py Parow Durbanville Observatory --destination Bellville --max-rent 9000
"""

import argparse

from dotenv import load_dotenv

from ct_housing.extract import is_safety_related
from ct_housing.models import UserPreferences
from ct_housing.research import research_area, searches_per_area
from ct_housing.scoring import rank
from ct_housing.search import CachedSearch


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("suburbs", nargs="+")
    parser.add_argument("--destination", required=True)
    parser.add_argument("--max-rent", type=int)
    parser.add_argument("--max-commute", type=int, default=60)
    parser.add_argument("--car", action="store_true")
    parser.add_argument("--lifestyle", choices=["quiet", "balanced", "lively"])
    parser.add_argument("--budget", type=int, default=40, help="max live SerpApi searches this run")
    args = parser.parse_args()

    prefs = UserPreferences(
        destination=args.destination,
        has_car=args.car,
        max_commute_min=args.max_commute,
        max_rent_zar=args.max_rent,
        lifestyle=args.lifestyle,
    )
    searcher = CachedSearch(max_live_searches=args.budget)
    print(f"Up to {searches_per_area(prefs)} searches per area (cached ones are free).")

    profiles = [research_area(searcher, s, prefs) for s in args.suburbs]
    print(f"Live searches used this run: {searcher.live_searches}\n")

    for i, rec in enumerate(rank(profiles, prefs), 1):
        overall = f"{rec.overall:.2f}" if rec.overall is not None else "n/a"
        print(f"#{i} {rec.suburb}  score {overall}  (evidence for {rec.coverage:.0%} of your priorities)")
        for d in rec.dealbreakers:
            print(f"   ⛔ {d}")
        for dim in rec.dimensions:
            score = f"{dim.score:.2f}" if dim.score is not None else "unknown"
            print(f"   • {dim.name:13} {score:8} {dim.summary}")
            for p in dim.positives:
                print(f"       + {p}")
            for n in dim.negatives:
                print(f"       − {n}")
            for u in dim.unknowns:
                print(f"       ? {u}")
        safety = [a for a in rec.profile.news if is_safety_related(a)]
        if safety:
            print(f"   📰 {len(safety)} safety-related news item(s) to read (not scored):")
            for a in safety[:3]:
                print(f"       - {a.result_date or 'undated'} | {a.source_name}: {a.title}")
        for link in rec.profile.browse_links[:2]:
            print(f"   🔗 Browse: {link.url}")
        print()


if __name__ == "__main__":
    main()
