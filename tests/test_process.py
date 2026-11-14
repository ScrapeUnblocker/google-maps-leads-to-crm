import pytest
from conftest import AUSTIN, ROUND_ROCK, SERP

from google_maps_leads.collect import Lead, lead_from_result
from google_maps_leads.config import Filters, Search
from google_maps_leads.process import (
    merge,
    pick_website,
    rank,
    registrable,
    reject_reason,
    search_words,
    select,
)

AUSTIN_SEARCH = Search("roofing contractors", "Austin, TX")
ROUND_ROCK_SEARCH = Search("roofing contractors", "Round Rock, TX")
QUERY = "roofing contractors in Austin, TX"


def leads_of(rows, search):
    return [lead_from_result(r, search) for r in rows]


def both_searches():
    return leads_of(AUSTIN, AUSTIN_SEARCH) + leads_of(ROUND_ROCK, ROUND_ROCK_SEARCH)


def test_merge_folds_phone_duplicates_across_searches():
    merged = merge(both_searches())
    names = [lead.name for lead in merged]
    assert names.count("Kidd Roofing") == 1
    kidd = next(lead for lead in merged if lead.name == "Kidd Roofing")
    assert kidd.searches == [QUERY, "roofing contractors in Round Rock, TX"]


def test_merge_folds_an_ad_into_the_normal_result():
    merged = merge(both_searches())
    lapeyre = [lead for lead in merged if lead.name == "Lapeyre Roofing"]
    assert len(lapeyre) == 1
    lead = lapeyre[0]
    # The normal result's phone wins; the ad's call-tracking number is kept as an alias.
    assert lead.phone == "(512) 877-3087"
    assert lead.other_phones == ["(512) 812-7501"]
    assert lead.street == "215 Brazos Street"
    assert lead.sponsored and not lead.from_ad
    assert "tel:5128127501" in lead.identities


def test_merge_matches_an_ad_from_another_towns_search():
    organic = lead_from_result(AUSTIN[2], AUSTIN_SEARCH)
    ad = lead_from_result(AUSTIN[0], ROUND_ROCK_SEARCH)
    assert len(merge([ad, organic])) == 1


def test_merge_keeps_same_named_businesses_in_different_towns():
    a = Lead(name="Ace Roofing Company", phone="(512) 111-1111", where="Austin, TX")
    b = Lead(name="Ace Roofing Company", phone="(214) 222-2222", where="Dallas, TX")
    assert len(merge([a, b])) == 2


@pytest.mark.parametrize(
    ("filters", "row", "reason"),
    [
        (Filters(min_rating=4.0), AUSTIN[4], "rating below 4"),
        (Filters(min_reviews=100), AUSTIN[3], "fewer than 100 reviews"),
        (Filters(max_reviews=500), AUSTIN[1], "more than 500 reviews"),
        (Filters(sponsored="exclude"), AUSTIN[0], "runs ads (sponsored: exclude)"),
        (Filters(sponsored="only"), AUSTIN[1], "no ads (sponsored: only)"),
        (Filters(categories=("dentist",)), AUSTIN[1], "category not in filters.categories"),
        (Filters(exclude_categories=("ROOFING",)), AUSTIN[1], "category excluded"),
        (Filters(exclude_names=("kidd",)), AUSTIN[1], "name excluded"),
        (Filters(categories=("roofing",)), AUSTIN[1], None),
        (Filters(), AUSTIN[1], None),
    ],
)
def test_reject_reason(filters, row, reason):
    assert reject_reason(lead_from_result(row, AUSTIN_SEARCH), filters) == reason


def test_require_phone():
    lead = Lead(name="No Phone Roofing", rating=5.0, reviews=50)
    assert reject_reason(lead, Filters(require_phone=True)) == "no phone"
    assert reject_reason(lead, Filters(require_phone=False)) is None


def test_rank_puts_most_reviews_first():
    ranked = rank(merge(both_searches()))
    assert [lead.reviews for lead in ranked] == sorted(
        (lead.reviews for lead in ranked), reverse=True
    )


def test_select_filters_and_skips_known_leads():
    merged = merge(both_searches())
    chosen = select(merged, Filters(min_rating=4.0, min_reviews=100), known=set())
    assert chosen.found == 5
    assert [lead.name for lead in chosen.new] == [
        "Kidd Roofing",
        "Dynamite Roofing & Construction",
        "Lapeyre Roofing",
    ]
    assert chosen.rejected == {"fewer than 100 reviews": 1, "rating below 4": 1}

    known = {"tel:5126717791", "ad:lapeyre roofing"}
    chosen = select(merged, Filters(min_rating=4.0, min_reviews=100), known=known)
    # Kidd by phone; Lapeyre because an ad with its name was delivered before.
    assert [lead.name for lead in chosen.new] == ["Dynamite Roofing & Construction"]
    assert len(chosen.passed) == 3


def test_a_delivered_normal_result_does_not_hide_a_same_named_one_elsewhere():
    elsewhere = Lead(name="Kidd Roofing", phone="(303) 000-0000", where="Denver, CO")
    chosen = select([elsewhere], Filters(), known={"brand:kidd roofing"})
    assert chosen.new == [elsewhere]


def test_registrable():
    assert registrable("www.kiddroof.com") == "kiddroof.com"
    assert registrable("shop.example.co.uk") == "example.co.uk"
    assert registrable("lapeyre-construction.wheree.com") == "wheree.com"


def test_search_words_include_singulars():
    words = search_words(QUERY)
    assert {"roofing", "contractors", "contractor", "austin", "tx"} <= words


def pick(lead, organic, query=QUERY):
    return pick_website(lead, organic, search_words(query))


def test_pick_website_finds_the_business_site():
    kidd = Lead(name="Kidd Roofing", phone="(512) 671-7791", locality="Austin, TX")
    assert pick(kidd, SERP["Kidd Roofing"]) == "https://www.kiddroof.com/"


def test_pick_website_skips_chambers_and_directories():
    lapeyre = Lead(
        name="Lapeyre Roofing",
        phone="(512) 877-3087",
        street="215 Brazos Street",
        locality="Austin, TX",
    )
    # The chamber page shows the phone but not the name in its domain; wheree is a
    # directory; the business's own site comes third.
    assert pick(lapeyre, SERP["Lapeyre Roofing"]) == "https://lapeyreroofing.com/"


def test_pick_website_needs_the_town_phone_or_street():
    lead = Lead(name="Clear Choice Roofing", phone="(512) 640-2643", locality="Austin, TX")
    other_town = {
        "title": "Clear Choice Roofing",
        "url": "https://clearchoiceroofingwtx.com/",
        "description": "Top-rated roofing in West Texas. (325) 267-8555",
    }
    assert pick(lead, [other_town]) is None
    right = {
        "title": "Clear Choice Roofing",
        "url": "https://www.clearchoiceroofingatx.com/emergency",
        "description": "They know Austin weather and get out fast to fix leaks.",
    }
    assert pick(lead, [other_town, right]) == "https://www.clearchoiceroofingatx.com/"


def test_pick_website_wants_the_brand_word():
    lead = Lead(
        name="Comfort Dental Mile High - Denver",
        phone="(303) 825-0013",
        street="881 Federal Blvd",
        where="Denver, CO",
    )
    results = [
        {
            "url": "https://milehighdentalcenters.com/",
            "title": "Dental Implants in Denver, CO | Mile High",
        },
        {
            "url": "https://comfortdental.com/denver",
            "title": "Comfort Dental - Colorado and Yale - Denver",
        },
    ]
    assert pick(lead, results, "dentists in Denver, CO") == "https://comfortdental.com/"


def test_pick_website_spelled_out_name_and_site_builders():
    lead = Lead(name="Austin Roofing and Construction", locality="Austin, TX")
    result = {
        "url": "https://www.austinroofingandconstruction.com/",
        "title": "Austin Roofing: Austin, TX Roofing Services",
    }
    assert pick(lead, [result]) == "https://www.austinroofingandconstruction.com/"

    ace = Lead(name="Ace Roofing Company", phone="(512) 836-7663", locality="Austin, TX")
    weebly = {
        "url": "https://aceroofingcompanytown.weebly.com/",
        "title": "ACE ROOFING COMPANY - home",
        "description": "9705 Burnet Road Suite 415 Austin, TX 78758 512-836-7663",
    }
    assert pick(ace, [weebly]) == "https://aceroofingcompanytown.weebly.com/"


def test_pick_website_none_without_a_match():
    lead = Lead(name="Kidd Roofing", phone="(512) 671-7791", locality="Austin, TX")
    assert pick(lead, []) is None
    assert pick(lead, [{"url": "https://www.yelp.com/biz/kidd-roofing-austin"}]) is None
    assert pick(lead, [{"url": "not a url", "title": "Kidd Roofing Austin"}]) is None
