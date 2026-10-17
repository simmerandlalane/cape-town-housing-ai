from datetime import UTC, datetime

import pytest

from ct_housing.extract import (
    clean_text,
    commute_from_directions,
    leads_from_search,
    parse_bedrooms,
    parse_minutes,
    parse_prices,
    parse_typical_range,
    staleness_warnings,
)
from ct_housing.models import SourceType

FETCHED = datetime(2026, 10, 17, 20, 0, tzinfo=UTC)


def test_clean_text_removes_zero_width_characters():
    assert clean_text("​R ​5 ​000") == "R 5 000"
    assert clean_text("7:13 AM") == "7:13 AM"
    assert clean_text(None) == ""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Glenlilly - Parow · ​R ​5 ​000 · Glenlilly ; · ​R ​6 ​200", [5000, 6200]),
        ("for rent starting 1 September 2025 for R7300 per month", [7300]),
        ("from R 6 000 / month. short term lease possible", [6000]),
        ("Valmary Park · R 9 200/month · 84 m²", [9200]),
        ("from R7965 to R50000", [7965, 50000]),
        ("R 21 500 per month … 10 300 per month 1 Bedroom", [21500, 10300]),  # second has no "R"
        ("R 8,600 · 29 Miracle Road, Cravenby", [8600]),
    ],
)
def test_parse_prices_real_formats(text, expected):
    assert parse_prices(text)[0] == expected


def test_parse_prices_ignores_phone_numbers_roads_and_queries():
    assert parse_prices("Contact 083 514 1542. Fully furnished")[0] == []
    assert parse_prices("via Voortrekker Rd/R102")[0] == []  # below range -> ignored
    assert parse_prices("flats for rent r2500 r3500")[0] == []  # lowercase r = search query text
    assert parse_prices("AVAILABLE: 900 Apartment / Flat")[0] == []


def test_parse_prices_reports_out_of_range_amounts():
    kept, ignored = parse_prices("R 125,000 · 84 Voortrekker Road ; R 8,600 · 29 Miracle Road")
    assert kept == [8600]
    assert ignored == [125000]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2 Bedroom Apartment To Rent in Observatory", [2]),
        ("Fully Furnished 2-Bedroom, 2-Bathroom", [2]),
        ("2 bed, 1 bath Vict", [2]),
        ("compact one-bedroom apartment", [1]),
        ("Browse 8 studio flats", [0]),
        ("1 Bedroom Apartment ... 2 Bedroom House", [1, 2]),
        ("38 apartments to rent in Parow", []),
    ],
)
def test_parse_bedrooms(text, expected):
    assert parse_bedrooms(text) == expected


def test_staleness_warnings():
    assert staleness_warnings("starting 1 September 2025", FETCHED) == ["Mentions 2025: may be outdated."]
    assert "has already passed" in staleness_warnings("Available from: 21/09/2026", FETCHED)[0]
    assert staleness_warnings("Available from: 01/12/2026", FETCHED) == []


def test_leads_keep_provenance_and_flag_spam(make_search_record):
    record = make_search_record(
        {
            "link": "https://www.property24.com/apartments-to-rent/parow/western-cape/436",
            "source": "Property24",
            "title": "Apartments / flats to rent in Parow",
            "snippet": "1 Bedroom Apartment / flat to rent in Glenlilly - Parow · ​R ​5 ​000",
        },
        {
            "link": "https://tbwydarqsqrx.pics/xuhsmuudznkyildljvjcrjmocy",
            "source": "tbwydarqsqrx.pics",
            "title": "Accommodation in durbanville cape town for rent",
            "snippet": "1 Bedroom Apartment To Rent in Durbanville Cape Town R 6 500 per month.",
            "date": "5 days ago",
        },
    )
    portal, spam = leads_from_search(record, "Parow")

    assert portal.source_type is SourceType.PORTAL_SEARCH_PAGE
    assert portal.reported_prices_zar == [5000]
    assert portal.evidence.search_id == "test-search-id"
    assert portal.evidence.query == "flat to rent in Parow Cape Town"
    assert portal.evidence.domain == "property24.com"

    assert spam.source_type is SourceType.UNKNOWN_SOURCE
    assert spam.known_source is False
    assert spam.evidence.result_date == "5 days ago"
    assert any("spam" in w for w in spam.warnings)


def test_leads_handle_incomplete_results(make_search_record):
    record = make_search_record(
        {"link": "https://www.immoafrica.net/to-rent/observatory/cape-town", "title": "Property & Homes To Rent"},
        {"title": "result without a link is skipped"},
    )
    [lead] = leads_from_search(record, "Observatory")
    assert lead.reported_prices_zar == []
    assert lead.price_range is None
    assert "Search result has no description." in lead.warnings


def test_parse_minutes_and_ranges():
    assert parse_minutes("43 min") == 43
    assert parse_minutes("1 hr 15 min") == 75
    assert parse_minutes("2 hr") == 120
    assert parse_typical_range("7–24 min") == (7, 24)
    assert parse_typical_range("40 min–1 hr 5 min") == (40, 65)
    assert parse_typical_range(None) is None


def _directions_record(directions=None, error=None):
    response = {"search_metadata": {"id": "dir-id", "google_maps_directions_url": "https://www.google.com/maps/dir/x"}}
    if directions is not None:
        response["directions"] = directions
    if error:
        response["error"] = error
    return {
        "fetched_at": "2026-10-17T20:30:00+00:00",
        "params": {"start_addr": "Parow, Cape Town", "end_addr": "Bellville, Cape Town", "time": "arrive_by:1"},
        "response": response,
    }


def test_commute_transit_route():
    record = _directions_record(
        [
            {
                "travel_mode": "Transit",
                "distance": 6066,
                "duration": 2533,
                "cost": 10,
                "currency": "ZAR",
                "start_time": "7:13 AM",
                "end_time": "7:56 AM",
                "trips": [
                    {"travel_mode": "Walking", "title": "Walk", "duration": 673},
                    {"travel_mode": "Transit", "title": "Northern Line-35s Wellington Station", "duration": 600},
                    {"travel_mode": "Walking", "title": "Walk", "duration": 1260},
                ],
            }
        ]
    )
    c = commute_from_directions(record, "Parow", "Bellville", "transit")
    assert (c.available, c.duration_min, c.distance_km, c.fare_zar) == (
        True,
        43,
        6.1,
        10,
    )  # Google rounds up: 2533 s shows as "43 min"
    assert c.lines == ["Northern Line-35s Wellington Station"]
    # Real case: of Parow's 43-minute "train" commute, 33 minutes is walking.
    assert (c.walk_min, c.longest_walk_min) == (33, 21)
    assert (c.depart_time, c.arrive_time) == ("7:13 AM", "7:56 AM")
    assert c.evidence.url == "https://www.google.com/maps/dir/x"


def test_commute_driving_has_traffic_range():
    record = _directions_record(
        [{"travel_mode": "Driving", "distance": 5129, "duration": 720, "typical_duration_range": "7–24 min"}]
    )
    c = commute_from_directions(record, "Parow", "Bellville", "driving")
    assert c.duration_min == 12
    assert c.typical_range_min == (7, 24)
    assert c.fare_zar is None


def test_commute_missing_route_is_unknown_not_zero():
    record = _directions_record(error="Google hasn't returned any results for this query.")
    c = commute_from_directions(record, "Durbanville", "Bellville", "transit")
    assert c.available is False
    assert c.duration_min is None
    assert "minibus taxis" in c.note


def test_amenities_count_only_places_in_the_suburb():
    from ct_housing.extract import amenities_from_maps

    record = {
        "fetched_at": "2026-10-17T20:00:00+00:00",
        "params": {"q": "gym in Parow, Cape Town"},
        "response": {
            "search_metadata": {"id": "m1"},
            "local_results": [
                {"title": "Virgin Active", "address": "Voortrekker Rd, Parow, Cape Town, 7500", "rating": 4.4},
                {"title": "Planet Fitness", "address": "Parow​ Centre, Cape Town", "rating": 4.8},
                {"title": "Elsewhere Gym", "address": "Bellville, Cape Town", "rating": 5.0},
            ],
        },
    }
    a = amenities_from_maps(record, "gym", "Parow")
    assert (a.count, a.average_rating) == (2, 4.6)
    assert a.examples == ["Virgin Active", "Planet Fitness"]


def test_news_flattens_stories_and_flags_safety():
    from ct_housing.extract import is_safety_related, news_from_search

    record = {
        "fetched_at": "2026-10-17T20:00:00+00:00",
        "params": {"q": '"Parow" Cape Town'},
        "response": {
            "news_results": [
                {
                    "title": "Father mourns daughter stabbed to death in Parow",
                    "link": "https://news24.com/a",
                    "source": {"name": "News24"},
                    "date": "03/31/2026",
                },
                {
                    "stories": [
                        {
                            "title": "SAPS hosts All Boys Parliament in Parow",
                            "link": "https://x.co.za/b",
                            "source": {"name": "Cape Town ETC"},
                        }
                    ]
                },
            ]
        },
    }
    first, second = news_from_search(record)
    assert first.source_name == "News24" and first.result_date == "03/31/2026"
    assert is_safety_related(first)
    assert not is_safety_related(second)  # mentioning SAPS alone is not a safety signal


def test_news_uses_iso_dates_newest_first_and_readable_source_names():
    from ct_housing.extract import news_from_search

    record = {
        "fetched_at": "2026-10-17T20:00:00+00:00",
        "params": {"q": '"Durbanville" Cape Town'},
        "response": {"news_results": [
            {"title": "Older", "link": "https://sabcnews.com/a", "source": {"name": "SABC News"},
             "iso_date": "2026-08-01T07:00:00Z"},
            {"title": "Newer", "link": "https://northernnews.co.za/b", "source": {"name": "https://northernnews.co.za/"},
             "iso_date": "2026-10-15T08:30:00Z"},
            {"title": "Undated", "link": "https://x.co.za/c", "source": {"name": "X"}},
        ]},
    }  # fmt: skip
    newer, older, undated = news_from_search(record)
    assert [newer.title, older.title, undated.title] == ["Newer", "Older", "Undated"]
    assert newer.source_name == "northernnews.co.za"
    assert newer.published_at.strftime("%d %b %Y") == "15 Oct 2026"
    assert undated.published_at is None


# ── Safety brief (fixtures trimmed from real October 2026 results) ──


def _safety_record(**response):
    return {"fetched_at": "2026-10-17T21:00:00+00:00", "params": {"q": "q"}, "response": response}


def test_resident_views_label_who_is_speaking_and_need_the_suburb_name():
    from ct_housing.extract import resident_views_from_search

    record = _safety_record(organic_results=[
        {"link": "https://www.reddit.com/r/capetown/comments/x/", "title": "Is Parow a safe neighbourhood to live in?"},
        {"link": "https://streetsignal.co.za/parow", "title": "Is Parow Safe? Crime Data", "snippet": "It scores 43/100"},
        {"link": "https://www.redproperties.co.za/parow", "title": "Parow Area Profile - Red Properties"},
        {"link": "https://www.quora.com/q", "title": "Is Cape Town safe for a young family?"},  # no suburb: dropped
    ])  # fmt: skip
    kinds = [v.kind for v in resident_views_from_search(record, "Parow")]
    assert kinds == ["discussion", "rating_site", "estate_agent"]


def test_stale_views_are_flagged():
    from ct_housing.extract import is_stale, resident_views_from_search

    record = _safety_record(organic_results=[
        {"link": "https://groundup.org.za/a", "title": "We're trying to improve Observatory", "date": "18 Apr 2016"},
        {"link": "https://x.co.za/b", "title": "Observatory guide", "date": "06 Oct 2026"},
    ])  # fmt: skip
    old, new = resident_views_from_search(record, "Observatory")
    assert is_stale(old.evidence) and not is_stale(new.evidence)


def test_patrols_come_from_local_places_and_web_results():
    from ct_housing.extract import patrol_groups_from_search

    # Real case: Observatory's groups only appeared in Google's local places box, not in web results.
    record = _safety_record(
        local_results={"places": [
            {"title": "Observatory Neighbourhood Watch", "address": "Observatory", "links": {"website": "http://www.obswatch.org.za/"}},
            {"title": "Observatory Improvement District", "address": "Observatory", "links": {"website": "https://www.obsid.org.za/"}},
        ]},
        organic_results=[
            {"link": "https://streetsignal.co.za/obs", "title": "Is Observatory in a City Improvement District?"},
            {"link": "https://www.westerncape.gov.za/nhw", "title": "Neighbourhood Watch Accreditation Programme"},
        ],
    )  # fmt: skip
    groups = patrol_groups_from_search(record, "Observatory")
    assert [(g.source_name, g.domain) for g in groups] == [
        ("Neighbourhood Watch", "obswatch.org.za"),
        ("City Improvement District", "obsid.org.za"),
    ]  # the rating site and the generic provincial page are excluded


def test_police_stations_only_state_police_named_suburb_first():
    from ct_housing.extract import police_stations_from_maps

    record = _safety_record(local_results=[
        {"title": "SAPS Ravensmead Police Station", "type": "State police", "address": "Ravensmead", "phone": "021 933 9000"},
        {"title": "S A Policing Union", "type": "Police department", "address": "Parow"},
        {"title": "SAPS Parow Police Station", "type": "State police", "address": "Parow East", "phone": "021 929 7000"},
        {"title": "City Of Cape Town Metropolitan Police Training Academy", "type": "Police academy"},
    ])  # fmt: skip
    stations = police_stations_from_maps(record, "Parow")
    assert [s.name for s in stations] == ["SAPS Parow Police Station", "SAPS Ravensmead Police Station"]
    assert stations[0].phone == "021 929 7000"


def test_police_exact_match_comes_from_place_results():
    from ct_housing.extract import police_stations_from_maps

    record = _safety_record(place_results={
        "title": "SAPS Durbanville Police Station", "type": ["State police", "Police department"],
        "address": "2 Church St, Durbanville", "phone": "021 970 3800",
    })  # fmt: skip
    [station] = police_stations_from_maps(record, "Durbanville")
    assert station.address == "2 Church St, Durbanville"
