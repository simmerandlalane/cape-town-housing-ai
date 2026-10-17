"""Explainable, per-dimension scoring of areas against one user's preferences.

Principles:
  - Each dimension scores 0..1 on its own, or None when evidence is missing. None is never 0.
  - The overall score averages only the dimensions we have evidence for, and `coverage` says
    how much of the user's priorities that represents. Low coverage = treat the rank with caution.
  - Safety is not scored. It's shown as dated evidence the user reads for themselves.
"""

from ct_housing.models import (
    AmenityCount,
    AreaProfile,
    AreaRecommendation,
    CommuteEstimate,
    DimensionScore,
    ListingLead,
    RentSummary,
    UserPreferences,
)

AMENITY_TARGET = 3  # places of a kind found in the area to count as "well served"
LIFESTYLES = ["quiet", "balanced", "lively"]
MIN_PRICES_FOR_CONFIDENCE = 3


def plural(word: str, n: int = 2) -> str:
    if n == 1:
        return word
    if word.endswith("y") and word[-2:-1] not in "aeiou":
        return word[:-1] + "ies"
    return word + ("es" if word.endswith(("s", "sh", "ch", "x")) else "s")


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def summarise_rent(leads: list[ListingLead]) -> RentSummary:
    """Pool snippet prices from trusted sources into an area-level indicator."""
    trusted = [lead for lead in leads if lead.known_source and lead.reported_prices_zar]
    return RentSummary(
        prices_zar=[p for lead in trusted for p in lead.reported_prices_zar],
        sources=[lead.evidence for lead in trusted],
    )


def score_affordability(rent: RentSummary, prefs: UserPreferences, weight: float) -> DimensionScore:
    name = "affordability"
    if prefs.max_rent_zar is None:
        return DimensionScore(name=name, score=None, weight=weight, summary="No budget set.")
    if not rent.prices_zar:
        return DimensionScore(
            name=name,
            score=None,
            weight=weight,
            summary="No rent prices found.",
            unknowns=["Rent level unknown: check Property24 for this area."],
        )

    within = [p for p in rent.prices_zar if p <= prefs.max_rent_zar]
    share = len(within) / len(rent.prices_zar)
    result = DimensionScore(
        name=name,
        score=share,
        weight=weight,
        summary=f"{len(within)} of {len(rent.prices_zar)} reported rents are within R{prefs.max_rent_zar:,} "
        f"(median R{rent.median_zar:,}).",
    )
    if share >= 0.5:
        result.positives.append(f"Most reported rents fit your budget (median R{rent.median_zar:,}).")
    elif within:
        result.negatives.append(f"Only some reported rents fit your budget (median R{rent.median_zar:,}).")
    else:
        result.negatives.append(f"No reported rents within budget (cheapest seen R{min(rent.prices_zar):,}).")
    if len(rent.prices_zar) < MIN_PRICES_FOR_CONFIDENCE:
        result.unknowns.append(f"Based on only {len(rent.prices_zar)} price(s) from search snippets.")
    return result


def _pick_commute(commutes: list[CommuteEstimate], prefs: UserPreferences) -> tuple[str, CommuteEstimate | None]:
    mode = "driving" if prefs.has_car else "transit"
    return mode, next((c for c in commutes if c.mode == mode), None)


def score_commute(
    commutes: list[CommuteEstimate], prefs: UserPreferences, weight: float
) -> tuple[DimensionScore, list[str]]:
    """Full marks up to half the user's max commute, falling linearly to 0 at the max."""
    name = "commute"
    mode, commute = _pick_commute(commutes, prefs)
    label = "public transport" if mode == "transit" else "car"

    if commute is None:
        return DimensionScore(
            name=name,
            score=None,
            weight=weight,
            summary=f"No {label} commute checked.",
            unknowns=[f"{label.capitalize()} commute to {prefs.destination} not checked."],
        ), []
    if not commute.available or commute.duration_min is None:
        return DimensionScore(
            name=name,
            score=None,
            weight=weight,
            summary=f"No {label} route found by Google Maps.",
            unknowns=[commute.note or f"No {label} route found."],
        ), []

    minutes, limit = commute.duration_min, prefs.max_commute_min
    ideal = limit / 2
    score = 1.0 if minutes <= ideal else max(0.0, (limit - minutes) / (limit - ideal))
    detail = f"{minutes} min by {label} to {prefs.destination}"
    if commute.lines:
        detail += f" ({', '.join(commute.lines)})"
    result = DimensionScore(name=name, score=round(score, 2), weight=weight, summary=detail + ".")

    dealbreakers = []
    if minutes > limit:
        dealbreakers.append(f"Commute {minutes} min exceeds your {limit} min maximum.")
        result.negatives.append(dealbreakers[0])
    elif minutes <= ideal:
        result.positives.append(f"Well within your {limit}-minute limit.")
    else:
        result.negatives.append(f"On the long side for your {limit}-minute limit.")
    if commute.typical_range_min:
        low, high = commute.typical_range_min
        result.unknowns.append(f"Traffic varies: typically {low}–{high} min.")
    if commute.note:
        result.unknowns.append(commute.note)
    return result, dealbreakers


def score_amenities(amenities: list[AmenityCount], prefs: UserPreferences, weight: float) -> DimensionScore:
    name = "amenities"
    found = {a.category: a for a in amenities}
    checked = [c for c in prefs.amenities_wanted if c in found]
    if not checked:
        return DimensionScore(
            name=name,
            score=None,
            weight=weight,
            summary="Amenities not checked.",
            unknowns=["No amenity searches for this area yet."],
        )

    result = DimensionScore(name=name, score=None, weight=weight, summary="")
    parts, well_served = [], []
    for category in checked:
        a = found[category]
        rating = f" (rated {a.average_rating:.1f})" if a.average_rating else ""
        parts.append(f"{a.count} {plural(category, a.count)}{rating}")
        if a.count >= AMENITY_TARGET:
            well_served.append(plural(category))
        elif a.count == 0:
            result.negatives.append(f"No {category} found in the area search.")
        else:
            result.negatives.append(f"Only {a.count} {plural(category, a.count)} found.")
    if well_served:
        result.positives.append(f"Well served for {_join(well_served)}.")
    for category in prefs.amenities_wanted:
        if category not in found:
            result.unknowns.append(f"{category.capitalize()} not checked.")

    result.score = round(sum(min(found[c].count / AMENITY_TARGET, 1.0) for c in checked) / len(checked), 2)
    result.summary = "Found " + _join(parts) + " in Google Maps results for this suburb (a lower bound)."
    return result


def score_lifestyle(profile: AreaProfile, prefs: UserPreferences, weight: float) -> DimensionScore:
    name = "lifestyle"
    if prefs.lifestyle is None:
        return DimensionScore(name=name, score=None, weight=weight, summary="No lifestyle preference set.")
    if profile.character not in LIFESTYLES:
        return DimensionScore(
            name=name,
            score=None,
            weight=weight,
            summary="Area character unknown.",
            unknowns=["Not enough sources to judge how quiet or lively this area is."],
        )

    distance = abs(LIFESTYLES.index(profile.character) - LIFESTYLES.index(prefs.lifestyle))
    score = {0: 1.0, 1: 0.5, 2: 0.0}[distance]
    result = DimensionScore(
        name=name,
        score=score,
        weight=weight,
        summary=f"Sources describe it as {profile.character}; you prefer {prefs.lifestyle}.",
        unknowns=["Character is summarised from web sources, and varies street by street."],
    )
    (result.positives if distance == 0 else result.negatives).append(result.summary)
    return result


def recommend(profile: AreaProfile, prefs: UserPreferences) -> AreaRecommendation:
    w = prefs.weights
    commute, dealbreakers = score_commute(profile.commutes, prefs, w.get("commute", 1.0))
    dimensions = [
        score_affordability(profile.rent, prefs, w.get("affordability", 1.0)),
        commute,
        score_amenities(profile.amenities, prefs, w.get("amenities", 1.0)),
        score_lifestyle(profile, prefs, w.get("lifestyle", 1.0)),
    ]
    # A dimension the user expressed no preference on is not one of their priorities.
    not_asked = {"affordability"} if prefs.max_rent_zar is None else set()
    if prefs.lifestyle is None:
        not_asked.add("lifestyle")
    for d in dimensions:
        d.asked = d.name not in not_asked
    active = [d for d in dimensions if d.weight > 0 and d.asked]
    known = [d for d in active if d.score is not None]
    total_weight = sum(d.weight for d in active)
    known_weight = sum(d.weight for d in known)

    # Snippet prices are examples, so only a reasonable sample of them can rule an area out.
    prices = profile.rent.prices_zar
    if prefs.max_rent_zar is not None and len(prices) >= MIN_PRICES_FOR_CONFIDENCE and min(prices) > prefs.max_rent_zar:
        dealbreakers.append(f"Every reported rent is above your R{prefs.max_rent_zar:,} budget.")

    return AreaRecommendation(
        suburb=profile.suburb,
        overall=round(sum(d.score * d.weight for d in known) / known_weight, 2) if known_weight else None,
        coverage=round(known_weight / total_weight, 2) if total_weight else 0.0,
        dimensions=dimensions,
        dealbreakers=dealbreakers,
        profile=profile,
    )


def _tie_breakers(rec: AreaRecommendation) -> tuple[float, float]:
    """Raw evidence for equal scores, e.g. two commutes both over the limit (both score 0): shorter wins."""
    commute = next((c.duration_min for c in rec.profile.commutes if c.available and c.duration_min), None)
    median = rec.profile.rent.median_zar
    return (commute if commute is not None else float("inf"), median if median is not None else float("inf"))


def rank(profiles: list[AreaProfile], prefs: UserPreferences) -> list[AreaRecommendation]:
    """Areas without dealbreakers first, then by overall score, then by evidence; unscorable areas last."""
    recs = [recommend(p, prefs) for p in profiles]
    return sorted(
        recs,
        key=lambda r: (bool(r.dealbreakers), r.overall is None, -(r.overall or 0), -r.coverage, *_tie_breakers(r)),
    )
