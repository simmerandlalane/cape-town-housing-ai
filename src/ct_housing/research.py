"""Deterministic research: the fixed set of searches run for every candidate area.

No AI here. The same inputs always produce the same searches, which keeps cost predictable
(see searches_per_area) and makes results cacheable and testable.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ct_housing.extract import (
    amenities_from_maps,
    commute_from_directions,
    leads_from_search,
    news_from_search,
    patrol_groups_from_search,
    police_stations_from_maps,
    resident_views_from_search,
)
from ct_housing.filters import HardConstraints, apply_hard_constraints, dedupe
from ct_housing.models import AreaProfile, SafetyBrief, SourceType, UserPreferences
from ct_housing.scoring import summarise_rent
from ct_housing.search import CachedSearch, slug

CITY = "Cape Town"
GOOGLE_LOCATION = "Cape Town,Western Cape,South Africa"
CAPE_TOWN_TZ = ZoneInfo("Africa/Johannesburg")
TRAVEL_MODE_CODES = {"transit": "3", "driving": "0"}  # SerpApi google_maps_directions travel_mode
BROWSE_DOMAINS = ("property24.com", "privateproperty.co.za")


def travel_mode(prefs: UserPreferences) -> str:
    return "driving" if prefs.has_car else "transit"


def cache_names(suburb: str, prefs: UserPreferences) -> dict[str, str]:
    """The cache file name of every search research_area() runs for this suburb."""
    key = slug(suburb)
    names = {
        "rent": f"listings-{key}",
        "commute": f"commute-{key}-{slug(prefs.destination)}-{travel_mode(prefs)}",
        "news": f"news-{key}",
        "safety_views": f"safety-views-{key}",
        "safety_patrols": f"safety-patrols-{key}",
        "police": f"police-{key}",
    }
    for category in prefs.amenities_wanted:
        names[f"amenity:{category}"] = f"amenity-{key}-{slug(category)}"
    return names


def searches_per_area(prefs: UserPreferences) -> int:
    return 6 + len(prefs.amenities_wanted)  # rent, commute, news, 3 safety searches, one per amenity


def new_searches_needed(searcher: CachedSearch, suburbs: list[str], prefs: UserPreferences) -> int:
    """How many live searches researching these suburbs would cost (cached ones are free)."""
    return sum(not searcher.is_cached(name) for suburb in suburbs for name in cache_names(suburb, prefs).values())


def arrive_by_timestamp(hhmm: str = "08:00") -> int:
    """Next Tuesday at hh:mm Cape Town wall-clock time, encoded the way SerpApi expects.

    SerpApi reads the timestamp as a GMT+0000 wall-clock time, so we label local time as UTC.
    """
    hour, minute = map(int, hhmm.split(":"))
    now = datetime.now(CAPE_TOWN_TZ)
    days_ahead = (1 - now.weekday()) % 7 or 7
    target = (now + timedelta(days=days_ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return int(target.replace(tzinfo=UTC).timestamp())


def research_area(searcher: CachedSearch, suburb: str, prefs: UserPreferences) -> AreaProfile:
    names = cache_names(suburb, prefs)
    profile = AreaProfile(suburb=suburb)

    # 1. Rent level and "browse" links
    record = searcher.search(
        names["rent"],
        {
            "engine": "google",
            "q": f"flat to rent in {suburb} {CITY}",
            "location": GOOGLE_LOCATION,
            "google_domain": "google.co.za",
            "gl": "za",
            "hl": "en",
        },
    )
    leads, _ = apply_hard_constraints(dedupe(leads_from_search(record, suburb)), HardConstraints())
    profile.rent = summarise_rent(leads)
    profile.browse_links = [
        lead.evidence
        for lead in leads
        if lead.source_type is SourceType.PORTAL_SEARCH_PAGE and lead.evidence.domain in BROWSE_DOMAINS
    ]

    # 2. Commute in the user's own travel mode, at weekday morning peak
    mode = travel_mode(prefs)
    record = searcher.search(
        names["commute"],
        {
            "engine": "google_maps_directions",
            "start_addr": f"{suburb}, {CITY}",
            "end_addr": f"{prefs.destination}, {CITY}",
            "travel_mode": TRAVEL_MODE_CODES[mode],
            "time": f"arrive_by:{arrive_by_timestamp()}",
            "distance_unit": "0",
            "hl": "en",
            "gl": "za",
        },
    )
    profile.commutes = [commute_from_directions(record, suburb, prefs.destination, mode)]

    # 3. Everyday amenities
    for category in prefs.amenities_wanted:
        record = searcher.search(
            names[f"amenity:{category}"],
            {
                "engine": "google_maps",
                "type": "search",
                "q": f"{category} in {suburb}, {CITY}",
                "hl": "en",
                "gl": "za",
            },
        )
        profile.amenities.append(amenities_from_maps(record, category, suburb))

    # 4. Recent local news (shown as evidence; safety-related items are flagged, never scored)
    record = searcher.search(
        names["news"],
        {
            "engine": "google_news",
            "q": f'"{suburb}" {CITY}',
            "gl": "za",
            "hl": "en",
        },
    )
    profile.news = news_from_search(record)

    # 5. Safety brief: what people say, patrol groups, and the nearest SAPS stations. Never scored.
    google = {"location": GOOGLE_LOCATION, "google_domain": "google.co.za", "gl": "za", "hl": "en"}
    views = searcher.search(
        names["safety_views"], {"engine": "google", "q": f"is {suburb} {CITY} safe to live", **google}
    )
    patrols = searcher.search(
        names["safety_patrols"],
        {
            "engine": "google",
            "q": f'{suburb} {CITY} "neighbourhood watch" OR "improvement district" OR "community police forum"',
            **google,
        },
    )
    police = searcher.search(
        names["police"],
        {"engine": "google_maps", "type": "search", "q": f"police station in {suburb}, {CITY}", "hl": "en", "gl": "za"},
    )
    profile.safety = SafetyBrief(
        resident_views=resident_views_from_search(views, suburb),
        patrol_groups=patrol_groups_from_search(patrols, suburb),
        police_stations=police_stations_from_maps(police, suburb),
        researched=True,
    )
    return profile
