"""Evaluate extraction, ranking and honesty against hand labels on real search results.

Offline: reads evals/fixtures (trimmed real results) and evals/gold.json (hand labels).
Writes docs/evaluation.md. Every mistake is listed, not just the totals.

Run:  uv run python evals/run.py
"""

import json
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ct_housing.extract import (
    clean_text,
    is_patrol_evidence,
    leads_from_search,
    patrol_candidates,
    police_stations_from_maps,
    resident_views_from_search,
)
from ct_housing.models import (
    AmenityCount,
    AreaProfile,
    CommuteEstimate,
    Evidence,
    RentSummary,
    UserPreferences,
)
from ct_housing.scoring import rank, recommend
from ct_housing.sources import registered_domain, view_kind

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals" / "fixtures"
GOLD = json.loads((ROOT / "evals" / "gold.json").read_text())
REPORT = ROOT / "docs" / "evaluation.md"
SUBURBS = {"parow": "Parow", "durbanville": "Durbanville", "observatory": "Observatory"}

# Phrases the app must never generate about an area (quoting a source is fine; asserting it ourselves is not).
VERDICT_PHRASES = re.compile(
    r"\b(is|are) (very |quite )?(safe|unsafe|dangerous)\b|\bsafe (area|suburb|neighbourhood)\b", re.I
)


@dataclass
class Result:
    name: str
    correct: int = 0
    total: int = 0
    failures: list[str] = field(default_factory=list)
    note: str = ""

    def check(self, ok: bool, failure: str) -> None:
        self.total += 1
        self.correct += ok
        if not ok:
            self.failures.append(failure)

    @property
    def score(self) -> float:
        return self.correct / self.total if self.total else 1.0


def fixture(name: str, key: str) -> dict:
    return json.loads((FIXTURES / f"{name}-{key}.json").read_text())


def short(url: str) -> str:
    return registered_domain(url) + (
        url.split(registered_domain(url), 1)[-1][:40] if registered_domain(url) in url else ""
    )


# ── Extraction tasks ─────────────────────────────────────────────────────────


def eval_listings() -> tuple[Result, Result, Result]:
    classes = Result("Source classification (spam, rented, short-term, results page, listing…)")
    precision = Result("Price extraction: precision (extracted amounts that are real rents)")
    recall = Result("Price extraction: recall (real rents that were extracted)")
    for key, suburb in SUBURBS.items():
        leads = leads_from_search(fixture("listings", key), suburb)
        gold = GOLD["listings"][key]
        if len(leads) != len(gold):
            raise ValueError(f"{key}: {len(leads)} leads vs {len(gold)} labels")
        for lead, g in zip(leads, gold, strict=True):
            where = f"{suburb}: {short(lead.evidence.url)}"
            classes.check(lead.source_type.value == g["source_type"],
                          f"{where}: predicted `{lead.source_type.value}`, expected `{g['source_type']}`")  # fmt: skip
            got, want = Counter(lead.reported_prices_zar), Counter(g["prices"])
            for amount in got:
                for _ in range(got[amount]):
                    precision.check(want[amount] > 0, f"{where}: extracted R{amount:,}, which isn't a rent")
                    want[amount] -= 1
            want = Counter(g["prices"])
            for amount in want:
                for _ in range(want[amount]):
                    recall.check(got[amount] > 0, f"{where}: missed R{amount:,}")
                    got[amount] -= 1
    return classes, precision, recall


def eval_resident_views() -> tuple[Result, Result]:
    relevance = Result("Resident views: is the result about this suburb?")
    kinds = Result("Resident views: who is speaking (residents, rating site, estate agent, news…)")
    for key, suburb in SUBURBS.items():
        record = fixture("safety-views", key)
        predicted = {v.evidence.url for v in resident_views_from_search(record, suburb, limit=100)}
        for result, g in zip(record["response"]["organic_results"], GOLD["resident_views"][key], strict=True):
            url, title = result["link"], clean_text(result.get("title"))
            relevance.check((url in predicted) == g["include"],
                            f"{suburb}: \"{title[:60]}\" predicted {'include' if url in predicted else 'exclude'}, "
                            f"expected {'include' if g['include'] else 'exclude'}")  # fmt: skip
            if g["include"]:
                kind = view_kind(registered_domain(url))
                kinds.check(
                    kind == g["kind"], f"{suburb}: {registered_domain(url)} labelled `{kind}`, expected `{g['kind']}`"
                )
    return relevance, kinds


def eval_patrols() -> Result:
    res = Result("Patrol groups: Neighbourhood Watch / CID / CPF evidence for this suburb")
    for key, suburb in SUBURBS.items():
        record = fixture("safety-patrols", key)
        gold = GOLD["patrol_groups"][key]
        labels = gold.get("local", []) + gold.get("organic", [])
        candidates = patrol_candidates(record)
        if len(candidates) != len(labels):
            raise ValueError(f"{key}: {len(candidates)} candidates vs {len(labels)} labels")
        for (_, url, title, text), label in zip(candidates, labels, strict=True):
            if label == "skip":
                continue
            found = is_patrol_evidence(url, title, text, suburb) is not None
            res.check(found == label, f'{suburb}: "{title[:60]}" predicted {found}, expected {label}')
    return res


def eval_police() -> Result:
    res = Result("Police: only SAPS stations kept, the suburb's own station first")
    for key, suburb in SUBURBS.items():
        stations = [s.name for s in police_stations_from_maps(fixture("police", key), suburb)]
        gold = GOLD["police"][key]
        for name in stations:
            res.check(name in gold["valid"], f"{suburb}: showed `{name}`, which is not a SAPS station")
        if gold["named"]:
            res.check(
                bool(stations) and stations[0] == gold["named"], f"{suburb}: own station not listed first ({stations})"
            )
        res.check(bool(stations), f"{suburb}: no station shown")
    return res


# ── Ranking invariants ───────────────────────────────────────────────────────

EV = Evidence(url="https://example.org", domain="example.org", fetched_at=datetime(2026, 10, 17, tzinfo=UTC))


def random_profile(rng: random.Random, name: str) -> AreaProfile:
    commute = None
    if rng.random() > 0.15:
        commute = CommuteEstimate(suburb=name, destination="X", mode="transit", available=True,
                                  duration_min=rng.randint(10, 110), evidence=EV)  # fmt: skip
    return AreaProfile(
        suburb=name,
        commutes=[commute] if commute else [],
        rent=RentSummary(prices_zar=[rng.randint(3000, 20000) for _ in range(rng.randint(0, 6))]),
        amenities=[
            AmenityCount(category=c, count=rng.randint(0, 8), evidence=EV)
            for c in ("gym", "park")
            if rng.random() > 0.2
        ],
    )


def eval_invariants(n: int = 400) -> Result:
    res = Result("Ranking invariants (randomised scenarios)")
    res.note = f"{n} random areas and preference sets, seed 7"
    rng = random.Random(7)  # noqa: S311 - reproducible test scenarios, not security
    for i in range(n):
        prefs = UserPreferences(destination="X", max_rent_zar=rng.choice([None, 6000, 9000, 14000]),
                                max_commute_min=rng.choice([30, 60, 90]), amenities_wanted=["gym", "park"])  # fmt: skip
        area = random_profile(rng, f"A{i}")
        rec = recommend(area, prefs)

        # 1. A cheaper extra listing never makes affordability worse
        if prefs.max_rent_zar and area.rent.prices_zar:
            cheaper = area.model_copy(deep=True)
            cheaper.rent.prices_zar.append(min(area.rent.prices_zar) - 500)
            before, after = rec.dimensions[0].score, recommend(cheaper, prefs).dimensions[0].score
            res.check(after >= before, f"case {i}: adding a cheaper rent lowered affordability {before}→{after}")

        # 2. A shorter commute never scores worse
        if area.commutes:
            faster = area.model_copy(deep=True)
            faster.commutes[0].duration_min = max(1, area.commutes[0].duration_min - 10)
            before, after = rec.dimensions[1].score, recommend(faster, prefs).dimensions[1].score
            res.check(after >= before, f"case {i}: a 10-min shorter commute scored worse {before}→{after}")

        # 3. Unknown dimensions are excluded, not counted as zero
        known = [d for d in rec.dimensions if d.asked and d.weight > 0 and d.score is not None]
        if known:
            expected = round(sum(d.score * d.weight for d in known) / sum(d.weight for d in known), 2)
            res.check(
                rec.overall == expected, f"case {i}: overall {rec.overall} != mean of known dimensions {expected}"
            )
        else:
            res.check(rec.overall is None, f"case {i}: no evidence but overall = {rec.overall}")

        # 4. Raising a priority favours the area that's better on it
        other = random_profile(rng, f"B{i}")
        if area.commutes and other.commutes and area.commutes[0].duration_min < other.commutes[0].duration_min - 15:
            heavy = prefs.model_copy(
                update={"weights": {"affordability": 0, "commute": 1, "amenities": 0, "lifestyle": 0}}
            )
            order = [r.suburb for r in rank([other, area], heavy)]
            if not recommend(area, heavy).dealbreakers or recommend(other, heavy).dealbreakers:
                res.check(order[0] == area.suburb, f"case {i}: commute-only ranking put the slower area first")
    return res


# ── Honesty guardrails ───────────────────────────────────────────────────────


def eval_guardrails() -> Result:
    res = Result("Honesty guardrails")
    # 1. Every safety item carries a source link and fetch time
    for key, suburb in SUBURBS.items():
        views = resident_views_from_search(fixture("safety-views", key), suburb, limit=100)
        stations = police_stations_from_maps(fixture("police", key), suburb)
        evidence = [v.evidence for v in views] + [s.evidence for s in stations]
        for e in evidence:
            res.check(e.url.startswith("https://") or e.url.startswith("http://"), f"{suburb}: item without a link")
            res.check(e.fetched_at is not None, f"{suburb}: item without a fetch time")
    # 2. The app's own wording never declares an area safe or unsafe
    own_text = (ROOT / "app.py").read_text() + (ROOT / "src" / "ct_housing" / "scoring.py").read_text()
    strings = re.findall(r'"([^"\n]{12,})"', own_text)
    for text in strings:
        res.check(not VERDICT_PHRASES.search(text), f'app text makes a safety verdict: "{text[:80]}"')
    # 3. Spam domains never feed rent levels
    for key, suburb in SUBURBS.items():
        for lead in leads_from_search(fixture("listings", key), suburb):
            if not lead.known_source:
                rent = rank([AreaProfile(suburb=suburb)], UserPreferences(destination="X"))  # smoke: no crash
                res.check(lead.source_type.value == "unknown_source", f"{suburb}: unknown site not marked untrusted")
                del rent
    return res


# ── Report ───────────────────────────────────────────────────────────────────


def write_report(results: list[Result]) -> None:
    lines = [
        "# Evaluation",
        "",
        f"Generated by `uv run python evals/run.py` on {datetime.now(UTC):%d %b %Y}. Offline and reproducible: it uses",
        "real search results fetched on 17 Oct 2026 (trimmed, in `evals/fixtures/`) and hand labels in",
        "[`evals/gold.json`](../evals/gold.json). Labels reflect what a careful reader would conclude; genuinely",
        "ambiguous items are marked `skip` and left out.",
        "",
        "| Check | Score | Correct / total |",
        "|---|---|---|",
    ]
    lines += [f"| {r.name} | **{r.score:.0%}** | {r.correct} / {r.total} |" for r in results]
    lines += ["", "## Every failure", ""]
    for r in results:
        if r.failures:
            lines += [f"**{r.name}**", ""] + [f"- {f}" for f in r.failures] + [""]
    if not any(r.failures for r in results):
        lines += ["None.", ""]
    lines += [
        "## What this does and doesn't show",
        "",
        "- **Small set:** 3 suburbs and about 80 real results. It catches regressions and shows failure modes; it is not",
        "  a statistically strong accuracy estimate.",
        "- **Possible label bias:** the labels were written by the developer after seeing the extractors' output, which",
        "  can bias labels towards agreeing with the code. 100% here means no mistakes were found on this set, not that",
        "  the extractors are perfect. New suburbs will surface new failure modes.",
        "- Commute, amenity and news extraction are covered by unit tests rather than this labelled set.",
        "- Safety *conclusions* are deliberately not evaluated, because the app doesn't draw any. Instead, the",
        "  guardrails check that it never asserts one, and that every safety item is traceable to a source.",
        "",
    ]
    REPORT.write_text("\n".join(lines))


def run() -> list[Result]:
    return [
        *eval_listings(),
        *eval_resident_views(),
        eval_patrols(),
        eval_police(),
        eval_invariants(),
        eval_guardrails(),
    ]


MIN_SCORE = 0.9  # CI fails below this

if __name__ == "__main__":
    import sys

    results = run()
    write_report(results)
    width = max(len(r.name) for r in results)
    for r in results:
        print(f"{r.name:<{width}}  {r.score:6.1%}  ({r.correct}/{r.total})")
    print(f"\nReport: {REPORT.relative_to(ROOT)}")
    if "--check" in sys.argv and (low := [r.name for r in results if r.score < MIN_SCORE]):
        sys.exit(f"Below {MIN_SCORE:.0%}: {', '.join(low)}")
