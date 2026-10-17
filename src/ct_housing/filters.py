"""Deduplicate leads and apply the user's hard constraints.

Rules of thumb:
  - Missing data never rejects a lead. Unknown price -> kept, with a warning.
  - Only a *single property* whose reported price breaks the budget is rejected.
    A results page ("38 apartments in Parow") is never rejected on its example snippet values.
"""

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pydantic import BaseModel

from ct_housing.models import ListingLead, SourceType

_TRACKING_PARAMS = {"gclid", "fbclid"}

DEFAULT_EXCLUDED_TYPES = frozenset(
    {
        SourceType.UNKNOWN_SOURCE,
        SourceType.ALREADY_RENTED,
        SourceType.SHORT_TERM,
    }
)


SINGLE_PROPERTY_TYPES = frozenset({SourceType.INDIVIDUAL_LISTING, SourceType.SOCIAL_POST})


class HardConstraints(BaseModel):
    # Bedrooms are deliberately not a constraint: the app recommends areas, and the user filters
    # flats by bedrooms on Property24. Bedroom counts only give context to rent levels.
    max_rent_zar: int | None = None
    excluded_types: frozenset[SourceType] = DEFAULT_EXCLUDED_TYPES


class Rejected(BaseModel):
    lead: ListingLead
    reason: str


def normalise_url(url: str) -> str:
    """Same page, different spelling -> same key. Keeps meaningful query params like ?page=3."""
    parts = urlparse(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if k not in _TRACKING_PARAMS and not k.startswith("utm_")]
    return urlunparse(
        (
            "https",
            parts.netloc.lower().removeprefix("www."),
            parts.path.rstrip("/") or "/",
            "",
            urlencode(sorted(query)),
            "",
        )
    )


def dedupe(leads: list[ListingLead]) -> list[ListingLead]:
    """Keep the first occurrence of each URL (search results are in rank order)."""
    seen: set[str] = set()
    unique = []
    for lead in leads:
        key = normalise_url(lead.evidence.url)
        if key not in seen:
            seen.add(key)
            unique.append(lead)
    return unique


def apply_hard_constraints(
    leads: list[ListingLead], constraints: HardConstraints
) -> tuple[list[ListingLead], list[Rejected]]:
    kept: list[ListingLead] = []
    rejected: list[Rejected] = []

    for lead in leads:
        if lead.source_type in constraints.excluded_types:
            rejected.append(Rejected(lead=lead, reason=f"Excluded source type: {lead.source_type.value}"))
            continue

        lead = lead.model_copy(deep=True)  # don't mutate the caller's objects when adding warnings
        # A results page's snippet shows a few examples, not everything on the page, so its
        # numbers can only warn. Only a single property can be rejected on price.
        single = lead.source_type in SINGLE_PROPERTY_TYPES

        if constraints.max_rent_zar is not None:
            budget = constraints.max_rent_zar
            prices = lead.price_range
            if prices is None:
                lead.warnings.append("Price unknown: check against your budget.")
            elif prices[0] > budget and single:
                rejected.append(Rejected(lead=lead, reason=f"Reported price R{prices[0]:,} exceeds budget R{budget:,}"))
                continue
            elif prices[0] > budget:
                lead.warnings.append(f"Example prices shown (from R{prices[0]:,}) are above your R{budget:,} budget.")
            elif prices[1] > budget:
                lead.warnings.append(f"Only some reported prices are within your R{budget:,} budget.")

        kept.append(lead)
    return kept, rejected
