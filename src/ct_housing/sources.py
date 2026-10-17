"""Which websites we recognise, and what kind of page a search result is.

The allowlist is deliberately explicit: a result from a domain not listed here is marked
UNKNOWN_SOURCE and rejected by default, because spam sites copy real listing text.
Add domains here (with care) when supporting more sources or cities.
"""

import re
from urllib.parse import urlparse

from ct_housing.models import SourceType

PROPERTY_PORTALS = {
    "property24.com",
    "privateproperty.co.za",
    "sahometraders.co.za",
    "property.mg.co.za",
    "myproperty.co.za",
    "propertycentral.co.za",
    "immoafrica.net",
    "findallrentals.co.za",
    "houses4rent.co.za",
    # aggregators that collect listings from other sites
    "homes.trovit.co.za",
    "homes.mitula.co.za",
    # estate agencies
    "rawson.co.za",
    "remaxliving.co.za",
    "roseeedes.co.za",
    "spectrumrealty.co.za",
    "pamgolding.co.za",
    "seeff.com",
    "jawitz.co.za",
    "chaseveritt.co.za",
    "redproperties.co.za",
}
CLASSIFIEDS = {"gumtree.co.za"}
SOCIAL = {"facebook.com"}
SHORT_TERM = {"airbnb.co.za", "airbnb.com", "booking.com"}

KNOWN_DOMAINS = PROPERTY_PORTALS | CLASSIFIEDS | SOCIAL | SHORT_TERM

# Where residents and newcomers discuss what living somewhere is like.
DISCUSSION_SITES = {"reddit.com", "mybroadband.co.za", "quora.com", "tripadvisor.com", "tripadvisor.co.za",
                    "facebook.com", "expatforum.com", "internations.org", "hellopeter.com"}  # fmt: skip
NEWS_SITES = {"news24.com", "iol.co.za", "timeslive.co.za", "dailymaverick.co.za", "groundup.org.za",
              "sabcnews.com", "capetownetc.com", "citizen.co.za", "ewn.co.za", "businesstech.co.za",
              "northernnews.co.za", "peoplespost.co.za", "tygerburger.co.za", "capeargus.co.za"}  # fmt: skip


# Sites that publish their own "safety score" for suburbs. Their methods aren't verifiable here.
SAFETY_RATING_SITES = {"streetsignal.co.za", "safesuburb.co.za", "capetowndata.com", "numbeo.com", "crimehub.org"}


def view_kind(domain: str) -> str:
    """Who is speaking: residents, an estate agent (may be promotional), a rating site, news, or someone else."""
    if _matches(domain, DISCUSSION_SITES):
        return "discussion"
    if _matches(domain, SAFETY_RATING_SITES):
        return "rating_site"
    if _matches(domain, PROPERTY_PORTALS):
        return "estate_agent"
    if _matches(domain, NEWS_SITES):
        return "news"
    return "other"


# "2 Bedroom Apartment To Rent in Observatory" (a single property) vs
# "38 apartments to rent in Parow" (a results page). Leading count => results page.
_SINGLE_PROPERTY_TITLE = re.compile(r"^\s*\d+\s*bed(room)?s?\b.*\b(to rent|for rent|to let)\b", re.I)


def registered_domain(url: str) -> str:
    """'https://www.property24.com/x' -> 'property24.com'. Keeps subdomains like homes.trovit.co.za."""
    host = urlparse(url).netloc.lower().split(":")[0]
    return host.removeprefix("www.")


def _matches(domain: str, known: set[str]) -> bool:
    return any(domain == k or domain.endswith("." + k) for k in known)


def is_known_domain(domain: str) -> bool:
    return _matches(domain, KNOWN_DOMAINS)


def classify(url: str, title: str | None) -> SourceType:
    domain = registered_domain(url)
    path = urlparse(url).path.lower()
    title = title or ""

    if not is_known_domain(domain):
        return SourceType.UNKNOWN_SOURCE
    if "/rented" in path or title.lower().startswith("rented"):
        return SourceType.ALREADY_RENTED
    if _matches(domain, SHORT_TERM):
        return SourceType.SHORT_TERM
    if _matches(domain, SOCIAL):
        return SourceType.SOCIAL_POST
    if _matches(domain, CLASSIFIEDS):
        return SourceType.CLASSIFIEDS
    if "/listing/" in path or _SINGLE_PROPERTY_TITLE.search(title):
        return SourceType.INDIVIDUAL_LISTING
    return SourceType.PORTAL_SEARCH_PAGE
