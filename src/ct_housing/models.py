"""Data models. Every fact the app shows must trace back to an Evidence record.

Provenance levels used across the app:
  REPORTED  - stated by a source (a search snippet, Google Maps). Not independently verified.
  ESTIMATED - calculated by this app from reported values (e.g. total monthly cost).
  MISSING   - we looked and found nothing. Shown as "unknown", never scored as zero.

Search engines cannot confirm that a listing is still available, so nothing here is "confirmed".
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Provenance(StrEnum):
    REPORTED = "reported"
    ESTIMATED = "estimated"
    MISSING = "missing"


class SourceType(StrEnum):
    PORTAL_SEARCH_PAGE = "portal_search_page"  # "38 apartments to rent in Parow"
    INDIVIDUAL_LISTING = "individual_listing"  # one specific property
    CLASSIFIEDS = "classifieds"  # Gumtree-style ads, quality varies
    SOCIAL_POST = "social_post"  # Facebook groups etc.; often stale
    SHORT_TERM = "short_term"  # Airbnb and similar, not a long lease
    ALREADY_RENTED = "already_rented"  # an agent's archive of let properties
    UNKNOWN_SOURCE = "unknown_source"  # domain we don't recognise; treat as untrusted


class Evidence(BaseModel):
    """Where a fact came from, kept so the user (and we) can check it."""

    url: str
    domain: str
    source_name: str | None = None
    title: str | None = None
    snippet: str | None = None
    result_date: str | None = None  # the search engine's own date label, e.g. "5 days ago"
    published_at: datetime | None = None  # parsed publish date when the source gives one (news)
    search_id: str | None = None  # SerpApi id; the full response can be re-fetched from the archive
    query: str | None = None
    fetched_at: datetime


class ListingLead(BaseModel):
    """A search result that may lead to rentals. All values are REPORTED by the snippet."""

    suburb: str
    source_type: SourceType
    known_source: bool
    reported_prices_zar: list[int] = Field(default_factory=list)  # monthly rent amounts seen
    bedrooms: list[int] = Field(default_factory=list)  # bedroom counts mentioned
    warnings: list[str] = Field(default_factory=list)
    evidence: Evidence

    @property
    def price_range(self) -> tuple[int, int] | None:
        if not self.reported_prices_zar:
            return None
        return min(self.reported_prices_zar), max(self.reported_prices_zar)


class CommuteEstimate(BaseModel):
    """A Google Maps route as reported for one travel mode at a given arrival time."""

    suburb: str
    destination: str
    mode: str  # "transit" | "driving"
    available: bool
    duration_min: int | None = None
    typical_range_min: tuple[int, int] | None = None  # driving only: Google's traffic range
    distance_km: float | None = None
    fare_zar: int | None = None
    lines: list[str] = Field(default_factory=list)  # e.g. ["Northern Line-35s Wellington Station"]
    walk_min: int | None = None  # total walking on a transit route (safety-relevant when walking alone)
    longest_walk_min: int | None = None
    depart_time: str | None = None  # e.g. "7:13 AM" for the arrive-by-08:00 route
    arrive_time: str | None = None
    start_coords: tuple[float, float] | None = None  # (lat, lon) as resolved by Google Maps
    end_coords: tuple[float, float] | None = None
    note: str | None = None  # why it's unavailable, or caveats
    evidence: Evidence


class RentSummary(BaseModel):
    """Rent levels in an area, pooled from snippet prices. An indicator, not a market survey."""

    prices_zar: list[int] = Field(default_factory=list)
    sources: list[Evidence] = Field(default_factory=list)

    @property
    def median_zar(self) -> int | None:
        if not self.prices_zar:
            return None
        ordered = sorted(self.prices_zar)
        mid = len(ordered) // 2
        return ordered[mid] if len(ordered) % 2 else round((ordered[mid - 1] + ordered[mid]) / 2)


class AmenityCount(BaseModel):
    """Places of one kind found by a Google Maps search in the area (a lower bound, not a census)."""

    category: str  # e.g. "grocery store"
    count: int
    average_rating: float | None = None
    examples: list[str] = Field(default_factory=list)
    evidence: Evidence


class ResidentView(BaseModel):
    """Something said about living in the area. Opinion, not fact, and labelled by who said it."""

    kind: str  # "discussion" (forums, Reddit) | "estate_agent" (may be promotional) | "news" | "other"
    evidence: Evidence


class PoliceStation(BaseModel):
    name: str
    address: str | None = None
    phone: str | None = None
    evidence: Evidence


class SafetyBrief(BaseModel):
    """Safety evidence for an area. Deliberately never reduced to a score."""

    resident_views: list[ResidentView] = Field(default_factory=list)
    patrol_groups: list[Evidence] = Field(default_factory=list)  # CIDs, Neighbourhood Watch, CPFs found in search
    police_stations: list[PoliceStation] = Field(default_factory=list)
    researched: bool = False  # False when safety searches weren't run (so "nothing found" isn't implied)


class AreaProfile(BaseModel):
    """Everything we gathered about one suburb. Missing parts stay empty / None."""

    suburb: str
    commutes: list[CommuteEstimate] = Field(default_factory=list)
    rent: RentSummary = Field(default_factory=RentSummary)
    amenities: list[AmenityCount] = Field(default_factory=list)
    character: str | None = None  # "quiet" | "balanced" | "lively", from the AI summary of sources
    character_summary: str | None = None
    character_sources: list[Evidence] = Field(default_factory=list)
    news: list[Evidence] = Field(default_factory=list)  # incl. safety-related; shown, never scored
    safety: SafetyBrief = Field(default_factory=SafetyBrief)
    browse_links: list[Evidence] = Field(default_factory=list)  # Property24 etc. results pages


class UserPreferences(BaseModel):
    destination: str
    has_car: bool = False
    max_commute_min: int = 60
    max_rent_zar: int | None = None
    lifestyle: str | None = None  # "quiet" | "balanced" | "lively" | None = no preference
    amenities_wanted: list[str] = Field(default_factory=lambda: ["grocery store", "gym", "park", "pharmacy"])
    # Example weights only: users set their own priorities. 0 = ignore that dimension.
    weights: dict[str, float] = Field(
        default_factory=lambda: {"affordability": 1.0, "commute": 1.0, "amenities": 1.0, "lifestyle": 1.0}
    )


class DimensionScore(BaseModel):
    name: str
    score: float | None  # 0..1, or None when we lack evidence (never treated as 0)
    weight: float
    summary: str
    positives: list[str] = Field(default_factory=list)
    negatives: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    asked: bool = True  # False when the user expressed no preference (e.g. no budget set)


class AreaRecommendation(BaseModel):
    suburb: str
    overall: float | None  # weighted mean of known dimensions
    coverage: float  # share of the user's total weight that had evidence
    dimensions: list[DimensionScore]
    dealbreakers: list[str] = Field(default_factory=list)
    profile: AreaProfile
