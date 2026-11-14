from google_maps_leads.collect import Lead
from google_maps_leads.messages import DISCORD, PLAIN, SLACK, Delivered, Digest, Outcome

SEARCHES = ["roofing contractors in Austin, TX", "roofing contractors in Round Rock, TX"]
LABELS = {"hubspot": "HubSpot", "airtable": "Airtable"}


def kidd(**kw):
    base = dict(
        name="Kidd Roofing",
        search=SEARCHES[0],
        where="Austin, TX",
        category="Roofing contractor",
        rating=4.9,
        reviews=870,
        phone="(512) 671-7791",
        locality="Austin, TX",
        years_in_business=40,
        website="https://www.kiddroof.com/",
        website_source="google_search",
        website_checked=True,
    )
    return Lead(**{**base, **kw})


def dynamite():
    return Lead(
        name="Dynamite Roofing & Construction",
        search=SEARCHES[1],
        where="Round Rock, TX",
        category="Roofing contractor",
        rating=4.9,
        reviews=362,
        phone="(512) 505-8008",
        locality="Round Rock, TX",
        website_checked=True,
    )


def digest(items, labels=LABELS, waiting=0):
    return Digest("2026-11-14", SEARCHES, items, dict(labels), waiting)


def created(lead):
    return Delivered(
        lead, {"hubspot": Outcome("created", "1"), "airtable": Outcome("created", "r")}
    )


def test_slack_digest():
    text = digest([created(kidd()), created(dynamite())]).chunks(SLACK, 3900)
    assert len(text) == 1
    assert text[0] == (
        "*2 new leads from Google Maps* · roofing contractors in Austin, TX and 1 more search\n"
        "Added to HubSpot (2) and Airtable (2)\n"
        "\n"
        "*Kidd Roofing* · Roofing contractor · 4.9★ (870 reviews)\n"
        "(512) 671-7791 · <https://www.kiddroof.com/|kiddroof.com> · Austin, TX · "
        "<https://www.google.com/maps/search/?api=1&query=Kidd+Roofing+Austin%2C+TX|Maps>\n"
        "40+ years in business\n"
        "\n"
        "*Dynamite Roofing &amp; Construction* · Roofing contractor · 4.9★ (362 reviews)\n"
        "(512) 505-8008 · Round Rock, TX · <https://www.google.com/maps/search/?api=1&query="
        "Dynamite+Roofing+%26+Construction+Round+Rock%2C+TX|Maps>\n"
        "no website found"
    )


def test_discord_escapes_markdown_and_hides_previews():
    lead = kidd(name="Kidd_Roofing *Pro*")
    text = digest([created(lead)]).chunks(DISCORD, 1990)[0]
    assert "**Kidd\\_Roofing \\*Pro\\***" in text
    assert "[kiddroof.com](<https://www.kiddroof.com/>)" in text


def test_plain_digest_shows_urls():
    text = digest([created(kidd())]).render(PLAIN)
    assert "(512) 671-7791 · https://www.kiddroof.com/ · Austin, TX" in text
    assert "Maps: https://www.google.com/maps/search/?api=1&query=Kidd+Roofing+Austin%2C+TX" in text


def test_leads_already_in_every_crm_are_counted_not_listed():
    there = Delivered(
        dynamite(), {"hubspot": Outcome("exists", "9"), "airtable": Outcome("exists", "r9")}
    )
    half = Delivered(
        kidd(), {"hubspot": Outcome("exists", "8"), "airtable": Outcome("created", "r")}
    )
    d = digest([there, half])
    assert [item.lead.name for item in d.listed] == ["Kidd Roofing"]
    assert d.crm_line() == "Added to Airtable (1) · 2 already in HubSpot · 1 already in Airtable"
    text = d.render(PLAIN)
    assert "Dynamite" not in text
    assert "already in HubSpot" in text


def test_dry_run_wording_and_waiting_note():
    item = Delivered(kidd(), {"hubspot": Outcome("would_create")})
    text = digest([item], {"hubspot": "HubSpot"}, waiting=3).render(PLAIN)
    assert "Would add to HubSpot (1)" in text
    assert "3 more new leads wait for the next run" in text


def test_chat_only_digest_has_no_crm_line():
    text = digest([Delivered(kidd())], {}).render(PLAIN)
    assert text.splitlines()[0] == (
        "1 new lead from Google Maps · roofing contractors in Austin, TX and 1 more search"
    )
    assert "Added to" not in text


def test_long_digests_are_split_into_posts():
    items = [created(kidd(name=f"Roofer {i}", phone=f"(512) 555-{i:04d}")) for i in range(30)]
    posts = digest(items).chunks(DISCORD, 1990)
    assert 1 < len(posts) <= 5
    assert all(len(p) <= 1990 for p in posts)
    assert posts[0].startswith("**30 new leads from Google Maps**")
    assert "Roofer 0" in posts[0]
    capped = digest(items).chunks(DISCORD, 1990, max_posts=2)
    assert len(capped) == 2 and "…and " in capped[-1]


def test_webhook_payload():
    payload = digest([created(kidd())]).payload()
    assert payload["source"] == "google-maps-leads-to-crm"
    assert payload["kind"] == "leads" and payload["count"] == 1
    assert payload["searches"] == SEARCHES
    lead = payload["leads"][0]
    assert lead["key"] == "tel:5126717791"
    assert lead["website"] == "https://www.kiddroof.com/"
    assert lead["signals"] == ["40+ years in business"]
    assert lead["crm"] == {
        "hubspot": {"status": "created", "id": "1"},
        "airtable": {"status": "created", "id": "r"},
    }
    assert payload["text"].startswith("1 new lead from Google Maps")
