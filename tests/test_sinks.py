import httpx
import pytest
from conftest import FakeHttp, FakeResponse

from google_maps_leads.collect import Lead
from google_maps_leads.config import (
    AirtableSettings,
    Delivery,
    HubSpotSettings,
    PipedriveSettings,
)
from google_maps_leads.messages import Delivered, Digest
from google_maps_leads.sinks import (
    AirtableSink,
    DeliveryError,
    DiscordSink,
    HubSpotSink,
    PipedriveSink,
    SlackSink,
    WebhookSink,
    build_sinks,
    request,
)

HS = "https://api.hubapi.com"
PD = "https://api.pipedrive.com"
AT = "https://api.airtable.com"


def kidd(**kw):
    base = dict(
        name="Kidd Roofing",
        search="roofing contractors in Austin, TX",
        where="Austin, TX",
        category="Roofing contractor",
        rating=4.9,
        reviews=870,
        phone="(512) 671-7791",
        locality="Austin, TX",
        years_in_business=40,
        website="https://www.kiddroof.com/",
        website_checked=True,
        first_seen="2026-11-14",
    )
    return Lead(**{**base, **kw})


# -- HubSpot ------------------------------------------------------------------------------


def test_hubspot_find_searches_phone_domain_and_name():
    http = FakeHttp({("POST", "/companies/search"): FakeResponse(200, {"results": []})})
    sink = HubSpotSink("tok", http, HubSpotSettings())
    assert sink.find(kidd()) is None
    sent = http.requests[0]
    assert sent["url"] == f"{HS}/crm/objects/2026-09/companies/search"
    assert sent["headers"]["Authorization"] == "Bearer tok"
    filters = [g["filters"][0] for g in sent["json"]["filterGroups"]]
    assert filters == [
        {"propertyName": "phone", "operator": "EQ", "value": "(512) 671-7791"},
        {"propertyName": "domain", "operator": "EQ", "value": "kiddroof.com"},
        {"propertyName": "name", "operator": "EQ", "value": "Kidd Roofing"},
    ]
    assert sent["json"]["limit"] == 1


def test_hubspot_find_returns_the_existing_company():
    http = FakeHttp({("POST", "/companies/search"): FakeResponse(200, {"results": [{"id": 7}]})})
    assert HubSpotSink("tok", http, HubSpotSettings()).find(kidd()) == "7"


def test_hubspot_create_maps_properties():
    http = FakeHttp({("POST", "/2026-09/companies"): FakeResponse(201, {"id": "123"})})
    sink = HubSpotSink("tok", http, HubSpotSettings())
    assert sink.create(kidd()) == "123"
    props = http.requests[0]["json"]["properties"]
    assert props["name"] == "Kidd Roofing"
    assert props["phone"] == "(512) 671-7791"
    assert props["domain"] == "kiddroof.com"
    assert props["website"] == "https://www.kiddroof.com/"
    assert (props["city"], props["state"]) == ("Austin", "TX")
    assert props["lifecyclestage"] == "lead"
    assert props["description"].startswith("Roofing contractor · 4.9★ (870 reviews)")
    assert "address" not in props  # no street from Google


def test_hubspot_custom_properties_and_no_lifecycle():
    settings = HubSpotSettings(
        properties={"name": "name", "google_rating": "rating"}, lifecycle_stage=""
    )
    sink = HubSpotSink("tok", FakeHttp(), settings)
    assert sink.preview(kidd()) == {"properties": {"name": "Kidd Roofing", "google_rating": "4.9"}}


def test_hubspot_error_is_reported():
    http = FakeHttp({("POST", "/companies/search"): FakeResponse(401, {"message": "bad token"})})
    with pytest.raises(DeliveryError, match="hubspot: HTTP 401"):
        HubSpotSink("tok", http, HubSpotSettings()).find(kidd())


# -- Pipedrive ----------------------------------------------------------------------------


def test_pipedrive_find_by_exact_name():
    found = {"success": True, "data": {"items": [{"item": {"id": 55, "name": "Kidd Roofing"}}]}}
    http = FakeHttp({("GET", "/api/v2/organizations/search"): FakeResponse(200, found)})
    sink = PipedriveSink("tok", http, PipedriveSettings())
    assert sink.find(kidd()) == "55"
    sent = http.requests[0]
    assert sent["headers"]["x-api-token"] == "tok"
    assert sent["params"] == {
        "term": "Kidd Roofing",
        "fields": "name",
        "exact_match": "true",
        "limit": 1,
    }


def test_pipedrive_create_org_note_and_lead():
    http = FakeHttp(
        {
            ("POST", "/api/v2/organizations"): FakeResponse(
                201, {"success": True, "data": {"id": 9}}
            ),
            ("POST", "/v1/notes"): FakeResponse(201, {"success": True, "data": {"id": 1}}),
            ("POST", "/v1/leads"): FakeResponse(201, {"success": True, "data": {"id": "u"}}),
        }
    )
    sink = PipedriveSink("tok", http, PipedriveSettings(custom_fields={"abc123": "rating"}))
    assert sink.create(kidd()) == "9"
    org = http.sent("POST", "/api/v2/organizations")[0]["json"]
    assert org == {
        "name": "Kidd Roofing",
        "address": {"value": "Austin, TX"},
        "website": "https://www.kiddroof.com/",
        "custom_fields": {"abc123": 4.9},
    }
    note = http.sent("POST", "/v1/notes")[0]["json"]
    assert note["org_id"] == 9
    assert note["content"].startswith("<p>Phone: (512) 671-7791<br>Roofing contractor")
    assert http.sent("POST", "/v1/leads")[0]["json"] == {
        "title": "Kidd Roofing",
        "organization_id": 9,
    }


def test_pipedrive_failed_note_is_a_warning_not_a_failure():
    http = FakeHttp(
        {
            ("POST", "/api/v2/organizations"): FakeResponse(
                201, {"success": True, "data": {"id": 9}}
            ),
            ("POST", "/v1/notes"): FakeResponse(403, {"success": False, "error": "no scope"}),
        }
    )
    sink = PipedriveSink("tok", http, PipedriveSettings(create_lead=False))
    assert sink.create(kidd()) == "9"
    assert sink.warnings and "organization 9 created" in sink.warnings[0]


# -- Airtable -----------------------------------------------------------------------------

TABLES = {
    "tables": [
        {
            "id": "tblLeads",
            "name": "Leads",
            "fields": [{"name": n} for n in ("Name", "Phone", "Rating", "Lead ID")],
        }
    ]
}


def airtable(http, **settings):
    return AirtableSink("tok", http, AirtableSettings(**settings), "appBase000000001")


def test_airtable_writes_only_existing_columns():
    http = FakeHttp(
        {
            ("GET", "/v0/meta/bases/appBase000000001/tables"): FakeResponse(200, TABLES),
            ("POST", "/v0/appBase000000001/tblLeads"): FakeResponse(
                200, {"records": [{"id": "rec1"}]}
            ),
        }
    )
    sink = airtable(http)
    assert sink.create(kidd()) == "rec1"
    body = http.sent("POST", "/tblLeads")[0]["json"]
    assert body == {
        "records": [
            {
                "fields": {
                    "Name": "Kidd Roofing",
                    "Phone": "(512) 671-7791",
                    "Rating": 4.9,
                    "Lead ID": "tel:5126717791",
                }
            }
        ],
        "typecast": True,
    }
    assert "has no column" in sink.warnings[0]
    assert len(http.sent("GET", "/tables")) == 1  # the schema is read once


def test_airtable_find_by_lead_id():
    http = FakeHttp(
        {
            ("GET", "/v0/meta/bases/appBase000000001/tables"): FakeResponse(200, TABLES),
            ("GET", "/v0/appBase000000001/tblLeads"): FakeResponse(
                200, {"records": [{"id": "recX"}]}
            ),
        }
    )
    assert airtable(http).find(kidd()) == "recX"
    params = http.sent("GET", "/tblLeads")[0]["params"]
    assert params == {"filterByFormula": '{Lead ID}="tel:5126717791"', "maxRecords": 1}


def test_airtable_without_the_merge_column_does_not_look_up():
    tables = {"tables": [{"id": "tblLeads", "name": "Leads", "fields": [{"name": "Name"}]}]}
    http = FakeHttp({("GET", "/tables"): FakeResponse(200, tables)})
    assert airtable(http).find(kidd()) is None
    assert len(http.requests) == 1


def test_airtable_without_schema_scope_writes_every_column():
    http = FakeHttp(
        {
            ("GET", "/tables"): FakeResponse(403, {"error": "INVALID_PERMISSIONS"}),
            ("POST", "/v0/appBase000000001/Leads%20Table"): FakeResponse(
                200, {"records": [{"id": "r"}]}
            ),
        }
    )
    sink = airtable(http, table="Leads Table")
    assert sink.create(kidd()) == "r"
    fields = http.sent("POST", "/Leads%20Table")[0]["json"]["records"][0]["fields"]
    assert fields["Google Maps"].startswith("https://www.google.com/maps/search/")
    assert fields["Signals"] == "40+ years in business"
    assert "schema.bases:read" in sink.warnings[0]


def test_airtable_missing_table():
    http = FakeHttp({("GET", "/tables"): FakeResponse(200, {"tables": []})})
    with pytest.raises(DeliveryError, match="no table named 'Leads'"):
        airtable(http).create(kidd())


# -- chat and webhook ---------------------------------------------------------------------


def digest():
    return Digest("2026-11-14", ["roofing contractors in Austin, TX"], [Delivered(kidd())])


def test_slack_discord_webhook_payloads():
    http = FakeHttp()
    SlackSink("https://hooks.slack.test/x", http).send(digest())
    DiscordSink("https://discord.test/api/webhooks/x", http).send(digest())
    WebhookSink("https://example.test/hook", http).send(digest())
    slack, discord, hook = (r["json"] for r in http.requests)
    assert slack["text"].startswith("*1 new lead from Google Maps*")
    assert slack["unfurl_links"] is False
    assert discord["content"].startswith("**1 new lead from Google Maps**")
    assert discord["allowed_mentions"] == {"parse": []} and discord["flags"] == 4
    assert hook["kind"] == "leads" and hook["leads"][0]["name"] == "Kidd Roofing"


def test_request_retries_rate_limits(no_sleep):
    http = FakeHttp(
        {("POST", "/x"): [FakeResponse(429, {}, {"Retry-After": "3"}), FakeResponse(200, {})]}
    )
    request(http, "POST", "https://example.test/x", sink="webhook", json={})
    assert len(http.requests) == 2 and no_sleep == [3.0]


def test_errors_never_show_the_webhook_url():
    class Down(FakeHttp):
        def request(self, method, url, **kwargs):
            raise httpx.ConnectError("refused")

    with pytest.raises(DeliveryError) as info:
        SlackSink("https://hooks.slack.test/T000/B000/secret", Down()).send(digest())
    assert "secret" not in str(info.value)
    assert str(info.value) == "slack: request failed (ConnectError)"


# -- registry -----------------------------------------------------------------------------


def test_build_sinks_uses_destinations_whose_secrets_are_set():
    env = {
        "HUBSPOT_TOKEN": "t",
        "AIRTABLE_TOKEN": "t",
        "SLACK_WEBHOOK_URL": "https://hooks.slack.test/x",
        "HUBSPOT_API_URL": "http://localhost:1",
    }
    sinks = build_sinks(Delivery(), env, FakeHttp())
    assert [s.name for s in sinks.crm] == ["hubspot"]
    assert [s.name for s in sinks.notify] == ["slack"]
    assert sinks.crm[0].base_url == "http://localhost:1"
    assert sinks.skipped == {
        "pipedrive": "PIPEDRIVE_API_TOKEN",
        "airtable": "delivery.airtable.base",
        "discord": "DISCORD_WEBHOOK_URL",
        "webhook": "LEADS_WEBHOOK_URL",
    }
    env["AIRTABLE_BASE_ID"] = "appBase000000001"
    assert [s.name for s in build_sinks(Delivery(), env, FakeHttp()).crm] == [
        "hubspot",
        "airtable",
    ]
