from datetime import date

import pytest
import scrapeunblocker as su
from conftest import AUSTIN, FakeClient

from google_maps_leads.collect import (
    FetchError,
    Lead,
    clean_name,
    lead_from_result,
    parse_address,
    search_businesses,
    search_web,
    website_query,
)
from google_maps_leads.config import Search

AUSTIN_SEARCH = Search("roofing contractors", "Austin, TX")


def test_clean_name_drops_the_ad_label():
    assert clean_name("Lapeyre Roofing My Ad Center") == "Lapeyre Roofing"
    assert clean_name("  Kidd   Roofing ") == "Kidd Roofing"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2121 S Oneida St #321", ("2121 S Oneida St #321", "", None)),
        ("10+ years in business · Austin, TX", ("", "Austin, TX", 10)),
        ("40+ years in business", ("", "", 40)),
        ("1 year in business · Denver, CO", ("", "Denver, CO", 1)),
        ("", ("", "", None)),
    ],
)
def test_parse_address(text, expected):
    assert parse_address(text) == expected


def test_lead_from_result_shapes_a_google_local_row():
    lead = lead_from_result(AUSTIN[1], AUSTIN_SEARCH, date(2026, 11, 14))
    assert lead is not None
    assert lead.name == "Kidd Roofing"
    assert (lead.rating, lead.reviews) == (4.9, 870)
    assert lead.phone == "(512) 671-7791"
    assert lead.locality == "Austin, TX" and lead.city == "Austin" and lead.region == "TX"
    assert lead.years_in_business == 40
    assert lead.key == "tel:5126717791"
    assert lead.search == "roofing contractors in Austin, TX"
    assert lead.first_seen == "2026-11-14"
    assert not lead.website_checked


def test_lead_from_result_marks_ads_and_24h():
    lead = lead_from_result(AUSTIN[0], AUSTIN_SEARCH)
    assert lead.sponsored and lead.from_ad and lead.open_24h
    assert lead.street == "215 Brazos Street"
    assert "runs Google Maps ads" in lead.signals()


def test_lead_from_result_keeps_a_website_google_shows():
    row = dict(AUSTIN[1], website="kiddroof.com")
    lead = lead_from_result(row, AUSTIN_SEARCH)
    assert lead.website == "https://kiddroof.com"
    assert lead.website_source == "google_maps" and lead.website_checked


@pytest.mark.parametrize("row", [None, "x", {}, {"name": ""}, {"name": " My Ad Center"}])
def test_lead_from_result_skips_rows_without_a_name(row):
    assert lead_from_result(row, AUSTIN_SEARCH) is None


def test_lead_without_phone_uses_a_name_key():
    lead = Lead(name="Joe's Roofing, LLC", where="Austin, TX")
    assert lead.key == "name:joe s roofing llc@austin tx"
    assert lead.identities[-1] == "brand:joe s roofing llc"


def test_maps_url_and_summary():
    lead = lead_from_result(AUSTIN[1], AUSTIN_SEARCH, date(2026, 11, 14))
    assert lead.maps_url == (
        "https://www.google.com/maps/search/?api=1&query=Kidd+Roofing+Austin%2C+TX"
    )
    summary = lead.summary()
    assert summary.splitlines()[0] == "Roofing contractor · 4.9★ (870 reviews)"
    assert 'Found on Google Maps for "roofing contractors in Austin, TX" on 2026-11-14' in summary
    assert "40+ years in business" in summary


def test_search_businesses_sends_country_and_language():
    client = FakeClient(local={"roofing contractors in Austin, TX": {"results": AUSTIN}})
    leads = search_businesses(client, AUSTIN_SEARCH, country="US", language="en")
    assert [lead.name for lead in leads][:2] == ["Lapeyre Roofing", "Kidd Roofing"]
    assert client.calls == [
        (
            "google_local",
            "roofing contractors in Austin, TX",
            {"proxy_country": "US", "gl": "us", "hl": "en"},
        )
    ]


def test_search_businesses_retries_a_transient_error_once(no_sleep):
    answers = [su.BlockedError("captcha", status_code=403), {"results": AUSTIN[:1]}]

    class Flaky(FakeClient):
        def google_local(self, keyword, **kwargs):
            self.calls.append(("google_local", keyword, kwargs))
            answer = answers.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer

    client = Flaky()
    leads = search_businesses(client, AUSTIN_SEARCH, country="US", language="en")
    assert len(leads) == 1 and client.count("google_local") == 2
    assert no_sleep == [5.0]


def test_search_businesses_gives_up_after_two_transient_errors():
    error = su.ServerError("no local results", status_code=502)
    client = FakeClient(local={"roofing contractors in Austin, TX": error})
    with pytest.raises(FetchError) as info:
        search_businesses(client, AUSTIN_SEARCH, country="US", language="en")
    assert not info.value.permanent
    assert client.count("google_local") == 2


def test_search_businesses_permanent_and_fatal_errors():
    bad = su.InvalidRequestError("bad keyword", status_code=422)
    client = FakeClient(local={"roofing contractors in Austin, TX": bad})
    with pytest.raises(FetchError) as info:
        search_businesses(client, AUSTIN_SEARCH, country="US", language="en")
    assert info.value.permanent and client.count("google_local") == 1

    auth = su.AuthenticationError("bad key", status_code=401)
    client = FakeClient(local={"roofing contractors in Austin, TX": auth})
    with pytest.raises(su.AuthenticationError):
        search_businesses(client, AUSTIN_SEARCH, country="US", language="en")


def test_search_businesses_rejects_an_odd_answer():
    client = FakeClient(local={"roofing contractors in Austin, TX": {"error": "?"}})
    with pytest.raises(FetchError):
        search_businesses(client, AUSTIN_SEARCH, country="US", language="en")


def test_website_query_and_search_web():
    lead = lead_from_result(AUSTIN[0], AUSTIN_SEARCH)
    assert website_query(lead) == "Lapeyre Roofing 215 Brazos Street"
    lead = lead_from_result(AUSTIN[1], AUSTIN_SEARCH)
    assert website_query(lead) == "Kidd Roofing Austin, TX"
    client = FakeClient(serps={"Kidd Roofing": [{"url": "https://www.kiddroof.com/"}, "junk"]})
    assert search_web(client, website_query(lead), country="US") == [
        {"url": "https://www.kiddroof.com/"}
    ]
    assert client.calls[0][2] == {"proxy_country": "US"}
