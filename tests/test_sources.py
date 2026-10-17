import pytest

from ct_housing.models import SourceType
from ct_housing.sources import classify, registered_domain


@pytest.mark.parametrize(
    ("url", "title", "expected"),
    [
        (
            "https://www.property24.com/apartments-to-rent/parow/western-cape/436",
            "Apartments / flats to rent in Parow",
            SourceType.PORTAL_SEARCH_PAGE,
        ),
        (
            "https://www.spectrumrealty.co.za/listing/1387504/apartment-to-rent",
            "2 Bedroom Apartment To Rent in Observatory, Cape Town",
            SourceType.INDIVIDUAL_LISTING,
        ),
        (
            "https://www.roseeedes.co.za/properties/rented-Observatory-Cape-Town-6017969",
            "Rented Properties in Observatory",
            SourceType.ALREADY_RENTED,
        ),
        (
            "https://www.facebook.com/groups/208865412478939/posts/1/",
            "Rental apartment in Durbanville available",
            SourceType.SOCIAL_POST,
        ),
        (
            "https://www.gumtree.co.za/s-property/observatory/flats",
            "Flats or granny flats for rent",
            SourceType.CLASSIFIEDS,
        ),
        (
            "https://www.airbnb.co.za/durbanville-cape-town-south-africa/stays/monthly",
            "Furnished Monthly Rentals",
            SourceType.SHORT_TERM,
        ),
        (
            "https://tbwydarqsqrx.pics/xuhsmuudznkyildljvjcrjmocy",
            "Accommodation in durbanville",
            SourceType.UNKNOWN_SOURCE,
        ),
        (
            "https://homes.trovit.co.za/for-rent-durbanville-flat",
            "407 flats to rent in Durbanville",
            SourceType.PORTAL_SEARCH_PAGE,
        ),
    ],
)
def test_classify_real_results(url, title, expected):
    assert classify(url, title) is expected


def test_lookalike_domain_is_not_trusted():
    assert classify("https://property24.com.scam.example/listing/1", "2 Bedroom To Rent") is SourceType.UNKNOWN_SOURCE
    assert classify("https://notproperty24.com/x", "x") is SourceType.UNKNOWN_SOURCE


def test_registered_domain():
    assert registered_domain("https://www.property24.com/x") == "property24.com"
    assert registered_domain("https://homes.mitula.co.za/homes") == "homes.mitula.co.za"
