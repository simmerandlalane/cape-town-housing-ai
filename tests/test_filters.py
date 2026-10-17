from datetime import UTC, datetime

from ct_housing.filters import HardConstraints, apply_hard_constraints, dedupe, normalise_url
from ct_housing.models import Evidence, ListingLead, SourceType


def lead(url="https://www.property24.com/x", prices=(), bedrooms=(), source_type=SourceType.PORTAL_SEARCH_PAGE):
    return ListingLead(
        suburb="Parow",
        source_type=source_type,
        known_source=source_type is not SourceType.UNKNOWN_SOURCE,
        reported_prices_zar=list(prices),
        bedrooms=list(bedrooms),
        evidence=Evidence(url=url, domain="property24.com", fetched_at=datetime(2026, 10, 17, tzinfo=UTC)),
    )


def test_normalise_url():
    assert normalise_url("http://WWW.Property24.com/a/?utm_source=x") == normalise_url("https://property24.com/a")
    assert normalise_url("https://x.co.za/p?page=3") != normalise_url("https://x.co.za/p")


def test_dedupe_keeps_first_in_rank_order():
    first, dup, other = (
        lead("https://www.property24.com/a/"),
        lead("https://property24.com/a"),
        lead("https://property24.com/b"),
    )
    assert dedupe([first, dup, other]) == [first, other]


def test_unknown_price_is_kept_with_warning():
    kept, rejected = apply_hard_constraints([lead()], HardConstraints(max_rent_zar=8000))
    assert len(kept) == 1 and not rejected
    assert "Price unknown: check against your budget." in kept[0].warnings


LISTING = SourceType.INDIVIDUAL_LISTING


def test_single_listing_over_budget_is_rejected():
    kept, rejected = apply_hard_constraints(
        [lead(prices=[16000], source_type=LISTING)], HardConstraints(max_rent_zar=8000)
    )
    assert not kept
    assert "exceeds budget" in rejected[0].reason


def test_results_page_over_budget_is_kept_with_warning():
    # Real case: "104 properties to rent in Durbanville ... R 16 000 per month" is one example of 104.
    cheap_and_dear, all_dear = lead(prices=[6300, 13000]), lead(prices=[16000])
    kept, rejected = apply_hard_constraints([cheap_and_dear, all_dear], HardConstraints(max_rent_zar=8000))
    assert not rejected
    assert any("Only some" in w for w in kept[0].warnings)
    assert any("above your R8,000 budget" in w for w in kept[1].warnings)


def test_bedrooms_are_informational_only():
    # The app recommends areas; users filter bedrooms on Property24. Never reject on bedrooms.
    kept, rejected = apply_hard_constraints([lead(bedrooms=[0], source_type=LISTING)], HardConstraints())
    assert not rejected
    assert kept[0].bedrooms == [0]


def test_default_exclusions():
    leads = [
        lead(source_type=t)
        for t in (SourceType.UNKNOWN_SOURCE, SourceType.ALREADY_RENTED, SourceType.SHORT_TERM, SourceType.SOCIAL_POST)
    ]
    kept, rejected = apply_hard_constraints(leads, HardConstraints())
    assert [k.source_type for k in kept] == [SourceType.SOCIAL_POST]
    assert len(rejected) == 3


def test_filters_do_not_mutate_input():
    original = lead()
    apply_hard_constraints([original], HardConstraints(max_rent_zar=8000))
    assert original.warnings == []
