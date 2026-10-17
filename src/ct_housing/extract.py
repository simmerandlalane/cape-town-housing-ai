"""Turn cached SerpApi responses into ListingLead / CommuteEstimate objects.

Input is a cache record as written by scripts/probe_search.py:
    {"fetched_at": ISO time, "params": {...}, "response": {...SerpApi JSON...}}
"""

import math
import re
from datetime import datetime

from ct_housing.models import (
    AmenityCount,
    CommuteEstimate,
    Evidence,
    ListingLead,
    PoliceStation,
    ResidentView,
    SourceType,
)
from ct_housing.sources import classify, is_known_domain, registered_domain, view_kind

# Plausible monthly rent for residential property. Outside this we ignore the number but say so:
# small values are usually road names (R302) and large ones sale or commercial prices.
MIN_RENT_ZAR = 1_500
MAX_RENT_ZAR = 80_000

_INVISIBLE = dict.fromkeys(map(ord, "​‌‍﻿"), None)
_ODD_SPACES = dict.fromkeys(map(ord, "   "), " ")

_NUMBER = r"(\d{1,3}(?:[ ,]\d{3})+|\d{4,6})(?![\d,])"
_PRICE_WITH_R = re.compile(r"\bR\s?" + _NUMBER)  # case-sensitive: "r2500" in a URL-ish title is a query
_PRICE_PER_MONTH = re.compile(r"(?<![\d,])(?<!R)(?<!R\s)" + _NUMBER + r"\s*(?:per month|/\s*month)", re.I)

_BEDROOMS = re.compile(r"\b(\d|one|two|three|four|five)\s*-?\s*bed(?:room)?s?\b", re.I)
_STUDIO = re.compile(r"\b(bachelor|studio)\b", re.I)
_WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_YEAR = re.compile(r"\b(20\d{2})\b")
_DMY_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b")

SOURCE_WARNINGS = {
    SourceType.PORTAL_SEARCH_PAGE: "Results page, not one property: prices are examples and change often.",
    SourceType.CLASSIFIEDS: "Classified ads vary in quality. Never pay a deposit before viewing in person.",
    SourceType.SOCIAL_POST: "Social media post: often outdated. Check the post date.",
    SourceType.SHORT_TERM: "Short-term / holiday rental platform, not a standard long lease.",
    SourceType.ALREADY_RENTED: "Page lists properties that are already rented.",
    SourceType.UNKNOWN_SOURCE: "Unrecognised website. Could be spam copying real listings.",
}


def clean_text(text: str | None) -> str:
    """Remove zero-width characters and normalise unusual spaces (Property24 uses both)."""
    if not text:
        return ""
    return re.sub(r" {2,}", " ", text.translate(_INVISIBLE).translate(_ODD_SPACES)).strip()


def _to_int(number: str) -> int:
    return int(number.replace(" ", "").replace(",", ""))


def parse_prices(text: str) -> tuple[list[int], list[int]]:
    """Return (plausible monthly rents, ignored out-of-range amounts) in order of appearance."""
    text = clean_text(text)
    matches = sorted(
        [(m.start(), _to_int(m.group(1))) for m in _PRICE_WITH_R.finditer(text)]
        + [(m.start(), _to_int(m.group(1))) for m in _PRICE_PER_MONTH.finditer(text)]
    )
    amounts = [amount for _, amount in matches]
    kept = [a for a in amounts if MIN_RENT_ZAR <= a <= MAX_RENT_ZAR]
    ignored = [a for a in amounts if not MIN_RENT_ZAR <= a <= MAX_RENT_ZAR]
    return kept, ignored


def parse_bedrooms(text: str) -> list[int]:
    """Distinct bedroom counts mentioned, sorted. Bachelor/studio counts as 0."""
    text = clean_text(text)
    counts = {int(n) if n.isdigit() else _WORD_NUMBERS[n.lower()] for n in _BEDROOMS.findall(text)}
    if _STUDIO.search(text):
        counts.add(0)
    return sorted(counts)


def staleness_warnings(text: str, fetched_at: datetime) -> list[str]:
    text = clean_text(text)
    warnings = []
    old_years = sorted({int(y) for y in _YEAR.findall(text) if int(y) < fetched_at.year})
    if old_years:
        warnings.append(f"Mentions {', '.join(map(str, old_years))}: may be outdated.")
    for day, month, year in _DMY_DATE.findall(text):
        try:
            date = datetime(int(year), int(month), int(day), tzinfo=fetched_at.tzinfo)
        except ValueError:
            continue
        if date < fetched_at:
            warnings.append(f"Availability date {day}/{month}/{year} has already passed.")
    return warnings


def leads_from_search(record: dict, suburb: str) -> list[ListingLead]:
    fetched_at = datetime.fromisoformat(record["fetched_at"])
    response = record["response"]
    search_id = response.get("search_metadata", {}).get("id")
    query = record.get("params", {}).get("q")

    leads = []
    for result in response.get("organic_results", []):
        url = result.get("link")
        if not url:
            continue
        title, snippet = clean_text(result.get("title")), clean_text(result.get("snippet"))
        text = f"{title}. {snippet}"
        source_type = classify(url, title)
        prices, ignored = parse_prices(text)

        warnings = [SOURCE_WARNINGS[source_type]] if source_type in SOURCE_WARNINGS else []
        if not snippet:
            warnings.append("Search result has no description.")
        if ignored:
            warnings.append(f"Ignored amounts outside R{MIN_RENT_ZAR:,}–R{MAX_RENT_ZAR:,}: {ignored}.")
        warnings += staleness_warnings(text, fetched_at)

        domain = registered_domain(url)
        leads.append(
            ListingLead(
                suburb=suburb,
                source_type=source_type,
                known_source=is_known_domain(domain),
                reported_prices_zar=prices,
                bedrooms=parse_bedrooms(text),
                warnings=warnings,
                evidence=Evidence(
                    url=url,
                    domain=domain,
                    source_name=result.get("source"),
                    title=title or None,
                    snippet=snippet or None,
                    result_date=result.get("date"),
                    search_id=search_id,
                    query=query,
                    fetched_at=fetched_at,
                ),
            )
        )
    return leads


def parse_minutes(text: str) -> int | None:
    """'43 min' -> 43, '1 hr 15 min' -> 75, '2 hr' -> 120."""
    hours = re.search(r"(\d+)\s*h", text)
    minutes = re.search(r"(\d+)\s*min", text)
    if not hours and not minutes:
        return None
    return (int(hours.group(1)) * 60 if hours else 0) + (int(minutes.group(1)) if minutes else 0)


def parse_typical_range(text: str | None) -> tuple[int, int] | None:
    """'7–24 min' -> (7, 24); '40 min–1 hr 5 min' -> (40, 65). A bare left number takes the right's unit."""
    if not text:
        return None
    left, sep, right = clean_text(text).replace("-", "–").partition("–")
    if not sep:
        return None
    high = parse_minutes(right)
    if high is None:
        return None
    low = parse_minutes(left)
    if low is None and left.strip().isdigit():
        low = int(left) * (60 if "min" not in right and "h" in right else 1)
    return (low, high) if low is not None else None


def _coords(place: dict) -> tuple[float, float] | None:
    gps = place.get("gps_coordinates") or {}
    lat, lon = gps.get("latitude"), gps.get("longitude")
    return (lat, lon) if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) else None


def commute_from_directions(record: dict, suburb: str, destination: str, mode: str) -> CommuteEstimate:
    response = record["response"]
    metadata = response.get("search_metadata", {})
    params = record.get("params", {})
    evidence = Evidence(
        url=metadata.get("google_maps_directions_url") or "https://www.google.com/maps",
        domain="google.com/maps",
        source_name="Google Maps (via SerpApi)",
        search_id=metadata.get("id"),
        query=f"{params.get('start_addr')} → {params.get('end_addr')} ({params.get('time')})",
        fetched_at=datetime.fromisoformat(record["fetched_at"]),
    )
    places = response.get("places_info") or []
    coords = {
        "start_coords": _coords(places[0]) if len(places) > 0 else None,
        "end_coords": _coords(places[-1]) if len(places) > 1 else None,
    }

    routes = response.get("directions") or []
    if not routes:
        return CommuteEstimate(
            **coords,
            suburb=suburb,
            destination=destination,
            mode=mode,
            available=False,
            note=f"No {mode} route returned by Google Maps ({response.get('error', 'no error given')}). "
            "This does not prove there is no option, e.g. minibus taxis are not in Google's data.",
            evidence=evidence,
        )

    best = routes[0]  # Google's recommended route
    note = "Timetable-based: actual train/bus reliability may differ." if mode == "transit" else None
    walks = [
        math.ceil(t["duration"] / 60)
        for t in best.get("trips", [])
        if t.get("travel_mode") == "Walking" and t.get("duration")
    ]
    has_walk_data = mode == "transit" and best.get("trips")
    return CommuteEstimate(
        **coords,
        walk_min=sum(walks) if has_walk_data else None,
        longest_walk_min=max(walks, default=0) if has_walk_data else None,
        depart_time=clean_text(best.get("start_time")) or None,
        arrive_time=clean_text(best.get("end_time")) or None,
        suburb=suburb,
        destination=destination,
        mode=mode,
        available=True,
        duration_min=math.ceil(best["duration"] / 60) if best.get("duration") else None,
        typical_range_min=parse_typical_range(best.get("typical_duration_range")),
        distance_km=round(best["distance"] / 1000, 1) if best.get("distance") else None,
        fare_zar=best.get("cost") if best.get("currency") == "ZAR" else None,
        lines=[t["title"] for t in best.get("trips", []) if t.get("travel_mode") == "Transit" and t.get("title")],
        note=note,
        evidence=evidence,
    )


SAFETY_KEYWORDS = re.compile(
    r"\b(crime|criminal|shooting|shot|murder|robbery|robbed|hijack\w*|burglar\w*|theft|stabb\w*|"
    r"gang\w*|arrest\w*|unsafe|violence|violent|assault\w*|mugg\w*)\b",
    re.I,
)


def amenities_from_maps(record: dict, category: str, suburb: str) -> AmenityCount:
    """Count places from a Google Maps search whose address mentions the suburb.

    Maps results can spill into neighbouring suburbs, so only addresses naming the suburb count.
    The count is a lower bound: one results page holds at most ~20 places.
    """
    response = record["response"]
    metadata = response.get("search_metadata", {})
    places = response.get("local_results") or []
    in_area = [p for p in places if suburb.lower() in clean_text(p.get("address")).lower()]
    ratings = [p["rating"] for p in in_area if isinstance(p.get("rating"), (int, float))]
    return AmenityCount(
        category=category,
        count=len(in_area),
        average_rating=round(sum(ratings) / len(ratings), 1) if ratings else None,
        examples=[clean_text(p.get("title")) for p in in_area[:3] if p.get("title")],
        evidence=Evidence(
            url=metadata.get("google_maps_url") or "https://www.google.com/maps",
            domain="google.com/maps",
            source_name="Google Maps (via SerpApi)",
            search_id=metadata.get("id"),
            query=record.get("params", {}).get("q"),
            fetched_at=datetime.fromisoformat(record["fetched_at"]),
        ),
    )


def news_from_search(record: dict, limit: int = 8) -> list[Evidence]:
    """News articles as Evidence. Some results group several articles under 'stories'."""
    response = record["response"]
    fetched_at = datetime.fromisoformat(record["fetched_at"])
    query = record.get("params", {}).get("q")
    items = []
    for result in response.get("news_results", []):
        items += result.get("stories") or [result]

    articles = []
    for item in items:
        if not item.get("link"):
            continue
        source = item.get("source")
        name = source.get("name") if isinstance(source, dict) else source
        if isinstance(name, str) and name.startswith(("http://", "https://")):
            name = registered_domain(name)  # some outlets have no display name, only a URL
        articles.append(
            Evidence(
                url=item["link"],
                domain=registered_domain(item["link"]),
                source_name=name,
                title=clean_text(item.get("title")) or None,
                snippet=clean_text(item.get("snippet")) or None,
                result_date=item.get("date"),
                published_at=_parse_iso(item.get("iso_date")),
                search_id=response.get("search_metadata", {}).get("id"),
                query=query,
                fetched_at=fetched_at,
            )
        )
    # Newest first; undated items last
    oldest = datetime.min.replace(tzinfo=fetched_at.tzinfo)
    articles.sort(key=lambda a: a.published_at or oldest, reverse=True)
    return articles[:limit]


def _parse_iso(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def is_safety_related(article: Evidence) -> bool:
    return bool(SAFETY_KEYWORDS.search(f"{article.title or ''} {article.snippet or ''}"))


# ── Safety brief ─────────────────────────────────────────────────────────────

PATROL_PATTERNS = {
    "Neighbourhood Watch": re.compile(r"neighbou?rhood\s+watch", re.I),
    "City Improvement District": re.compile(r"improvement\s+district|\bCID\b", re.I),
    "Community Police Forum": re.compile(r"community\s+police\s+forum|\bCPF\b", re.I),
}
STALE_AFTER_YEARS = 2


def _local_places(response: dict) -> list[dict]:
    """Google's local "places" box: a list, or a dict with a "places" list, depending on the query."""
    local = response.get("local_results") or []
    return local.get("places", []) if isinstance(local, dict) else local


def _mentions(text: str, suburb: str) -> bool:
    return suburb.lower() in text.lower()


def _search_evidence(record: dict, result: dict, url: str, title: str, snippet: str) -> Evidence:
    response = record["response"]
    return Evidence(
        url=url,
        domain=registered_domain(url),
        source_name=result.get("source"),
        title=title or None,
        snippet=snippet or None,
        result_date=result.get("date"),
        search_id=response.get("search_metadata", {}).get("id"),
        query=record.get("params", {}).get("q"),
        fetched_at=datetime.fromisoformat(record["fetched_at"]),
    )


def resident_views_from_search(record: dict, suburb: str, limit: int = 6) -> list[ResidentView]:
    """What people and websites say about living in the suburb. Only results that name the suburb count."""
    views = []
    for result in record["response"].get("organic_results", []):
        url = result.get("link")
        title, snippet = clean_text(result.get("title")), clean_text(result.get("snippet"))
        if not url or not _mentions(f"{title} {snippet} {url}", suburb):
            continue
        views.append(ResidentView(
            kind=view_kind(registered_domain(url)),
            evidence=_search_evidence(record, result, url, title, snippet),
        ))  # fmt: skip
    return views[:limit]


def is_stale(evidence: Evidence, years: int = STALE_AFTER_YEARS) -> bool:
    years_seen = [int(y) for y in _YEAR.findall(f"{evidence.result_date or ''}")]
    return bool(years_seen) and evidence.fetched_at.year - max(years_seen) >= years


def patrol_kind(text: str) -> str | None:
    return next((kind for kind, pattern in PATROL_PATTERNS.items() if pattern.search(text)), None)


def patrol_candidates(record: dict) -> list[tuple[dict, str, str, str]]:
    """(result, url, title, text) for Google's local places box first, then web results."""
    candidates = []
    for place in _local_places(record["response"]):
        links = place.get("links") if isinstance(place.get("links"), dict) else {}
        url = links.get("website") or place.get("website")
        if url:
            candidates.append((place, url, clean_text(place.get("title")), clean_text(place.get("address"))))
    for result in record["response"].get("organic_results", []):
        if result.get("link"):
            candidates.append(
                (result, result["link"], clean_text(result.get("title")), clean_text(result.get("snippet")))
            )
    return candidates


def is_patrol_evidence(url: str, title: str, text: str, suburb: str) -> str | None:
    """The kind of patrol group this result is evidence of for the suburb, or None."""
    kind = patrol_kind(f"{title} {text}")
    if not kind or not _mentions(f"{title} {text} {url}", suburb):
        return None
    if view_kind(registered_domain(url)) == "rating_site":
        return None  # a rating site's claim about a CID is not the organisation itself
    return kind


def patrol_groups_from_search(record: dict, suburb: str) -> list[Evidence]:
    """Neighbourhood Watches, City Improvement Districts and Community Police Forums named after the suburb.

    Reads both web results and Google's local places box (some areas only appear in the latter).
    """
    groups, seen = [], set()
    for result, url, title, text in patrol_candidates(record):
        kind = is_patrol_evidence(url, title, text, suburb)
        if not kind or title.lower() in seen:
            continue
        seen.add(title.lower())
        evidence = _search_evidence(record, result, url, title, text)
        evidence.source_name = kind
        groups.append(evidence)
    return groups


def police_stations_from_maps(record: dict, suburb: str) -> list[PoliceStation]:
    """SAPS stations from a Google Maps search: one exact match (place_results) or a list (local_results).

    Only state police stations count; unions, academies and law-enforcement offices are excluded.
    """
    response = record["response"]
    metadata = response.get("search_metadata", {})
    places = [response["place_results"]] if response.get("place_results") else (response.get("local_results") or [])
    stations = []
    for place in places:
        title = clean_text(place.get("title"))
        types = place.get("type") if isinstance(place.get("type"), list) else [place.get("type") or ""]
        if not (title.upper().startswith("SAPS") or any("state police" in t.lower() for t in types)):
            continue
        if "academy" in title.lower() or "union" in title.lower():
            continue
        stations.append(PoliceStation(
            name=title,
            address=clean_text(place.get("address")) or None,
            phone=clean_text(place.get("phone")) or None,
            evidence=Evidence(
                url=metadata.get("google_maps_url") or "https://www.google.com/maps",
                domain="google.com/maps",
                source_name="Google Maps (via SerpApi)",
                search_id=metadata.get("id"),
                query=record.get("params", {}).get("q"),
                fetched_at=datetime.fromisoformat(record["fetched_at"]),
            ),
        ))  # fmt: skip
    # Stations named after the suburb first, then the others as "nearby"
    stations.sort(key=lambda s: not _mentions(s.name, suburb))
    return stations[:3]
