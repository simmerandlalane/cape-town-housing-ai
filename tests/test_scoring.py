from datetime import UTC, datetime

from ct_housing.models import (
    AmenityCount,
    AreaProfile,
    CommuteEstimate,
    Evidence,
    ListingLead,
    RentSummary,
    SourceType,
    UserPreferences,
)
from ct_housing.scoring import rank, recommend, score_affordability, score_amenities, score_commute, summarise_rent

EV = Evidence(url="https://example.org", domain="example.org", fetched_at=datetime(2026, 10, 17, tzinfo=UTC))


def commute(mode, minutes, available=True, **kw):
    return CommuteEstimate(
        suburb="X", destination="Bellville", mode=mode, available=available, duration_min=minutes, evidence=EV, **kw
    )


def prefs(**kw):
    return UserPreferences(destination="Bellville", **kw)


def test_rent_summary_uses_only_trusted_sources():
    trusted = ListingLead(
        suburb="Parow",
        source_type=SourceType.PORTAL_SEARCH_PAGE,
        known_source=True,
        reported_prices_zar=[5000, 6200],
        evidence=EV,
    )
    spam = ListingLead(
        suburb="Parow",
        source_type=SourceType.UNKNOWN_SOURCE,
        known_source=False,
        reported_prices_zar=[1500],
        evidence=EV,
    )
    rent = summarise_rent([trusted, spam])
    assert rent.prices_zar == [5000, 6200]
    assert rent.median_zar == 5600


def test_affordability_unknown_is_none_not_zero():
    d = score_affordability(RentSummary(), prefs(max_rent_zar=8000), 1.0)
    assert d.score is None
    assert d.unknowns


def test_affordability_share_within_budget_and_low_sample_warning():
    d = score_affordability(RentSummary(prices_zar=[6000, 10000]), prefs(max_rent_zar=8000), 1.0)
    assert d.score == 0.5
    assert any("only 2" in u for u in d.unknowns)


def test_commute_uses_mode_matching_car_ownership():
    commutes = [commute("transit", 43, lines=["Northern Line"]), commute("driving", 12)]
    no_car, _ = score_commute(commutes, prefs(max_commute_min=60), 1.0)
    car, _ = score_commute(commutes, prefs(has_car=True, max_commute_min=60), 1.0)
    assert no_car.score == 0.57  # 43 min: between ideal (30) and max (60)
    assert "Northern Line" in no_car.summary
    assert car.score == 1.0


def test_commute_over_max_is_dealbreaker():
    d, dealbreakers = score_commute([commute("transit", 71)], prefs(max_commute_min=60), 1.0)
    assert d.score == 0.0
    assert dealbreakers == ["Commute 71 min exceeds your 60 min maximum."]


def test_missing_transit_route_is_unknown_not_dealbreaker():
    # Real case: Durbanville -> Bellville returned no transit route.
    d, dealbreakers = score_commute([commute("transit", None, available=False, note="No route")], prefs(), 1.0)
    assert d.score is None and not dealbreakers


def test_amenities_partial_and_unchecked():
    amenities = [
        AmenityCount(category="gym", count=5, average_rating=4.4, evidence=EV),
        AmenityCount(category="park", count=1, evidence=EV),
    ]
    d = score_amenities(amenities, prefs(amenities_wanted=["gym", "park", "pharmacy"]), 1.0)
    assert d.score == round((1.0 + 1 / 3) / 2, 2)
    assert any("Pharmacy not checked" in u for u in d.unknowns)


def test_overall_averages_known_dimensions_and_reports_coverage():
    profile = AreaProfile(suburb="Parow", commutes=[commute("transit", 25)])  # only commute is known
    rec = recommend(profile, prefs(max_rent_zar=8000, lifestyle="quiet"))
    assert rec.overall == 1.0
    assert rec.coverage == 0.25  # 1 of 4 equally weighted dimensions had evidence


def test_zero_weight_dimension_is_ignored():
    profile = AreaProfile(suburb="Parow", commutes=[commute("transit", 25)])
    weights = {"affordability": 0, "commute": 1, "amenities": 0, "lifestyle": 0}
    rec = recommend(profile, prefs(weights=weights))
    assert rec.coverage == 1.0


def test_rank_puts_dealbreakers_and_unknowns_last():
    good = AreaProfile(suburb="Parow", commutes=[commute("transit", 43)])
    far = AreaProfile(suburb="Observatory", commutes=[commute("transit", 71)])
    unknown = AreaProfile(suburb="Durbanville")
    better = AreaProfile(suburb="Bellville", commutes=[commute("transit", 15)])
    order = [r.suburb for r in rank([far, unknown, good, better], prefs(max_commute_min=60))]
    assert order == ["Bellville", "Parow", "Durbanville", "Observatory"]


def test_dimensions_without_a_preference_do_not_reduce_coverage():
    profile = AreaProfile(
        suburb="Parow",
        commutes=[commute("transit", 25)],
        amenities=[AmenityCount(category="gym", count=3, evidence=EV)],
    )
    rec = recommend(profile, prefs(amenities_wanted=["gym"]))  # no budget, no lifestyle preference
    assert rec.coverage == 1.0


def test_plural():
    from ct_housing.scoring import plural

    assert [plural("pharmacy"), plural("gym"), plural("grocery store"), plural("gym", 1)] == [
        "pharmacies",
        "gyms",
        "grocery stores",
        "gym",
    ]


def test_few_snippet_prices_over_budget_are_not_a_dealbreaker():
    # Real case: Observatory had only 2 example prices (R13,500 and R16,000) from results pages.
    few = AreaProfile(suburb="Observatory", rent=RentSummary(prices_zar=[13500, 16000]))
    many = AreaProfile(suburb="X", rent=RentSummary(prices_zar=[13500, 16000, 14000]))
    assert not recommend(few, prefs(max_rent_zar=9000)).dealbreakers
    assert recommend(many, prefs(max_rent_zar=9000)).dealbreakers


def test_ties_are_broken_by_evidence_not_input_order():
    # Found by evals/run.py: two areas both over the commute limit both score 0 and kept input order.
    slightly_over = AreaProfile(suburb="Near", commutes=[commute("transit", 33)])
    far_over = AreaProfile(suburb="Far", commutes=[commute("transit", 96)])
    order = [r.suburb for r in rank([far_over, slightly_over], prefs(max_commute_min=30))]
    assert order == ["Near", "Far"]
