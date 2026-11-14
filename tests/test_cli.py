import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import scrapeunblocker as su
from conftest import FakeClient, FakeHttp, FakeResponse

from google_maps_leads.cli import build_parser, main, pick_searches, run_leads
from google_maps_leads.config import Search
from google_maps_leads.sinks import Sinks, build_sinks

NOW = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
ENV = {
    "SCRAPEUNBLOCKER_KEY": "test-key",
    "HUBSPOT_TOKEN": "hs",
    "SLACK_WEBHOOK_URL": "https://hooks.slack.test/x",
    "LEADS_WEBHOOK_URL": "https://example.test/hook",
}


class Crm:
    """A HubSpot that remembers what it created, so duplicate checks work across runs."""

    def __init__(self):
        self.companies = []

    def search(self, kwargs):
        wanted = {
            (f["propertyName"], str(f["value"]).lower())
            for g in kwargs["json"]["filterGroups"]
            for f in g["filters"]
        }
        hits = [
            c
            for c in self.companies
            if any((k, str(v).lower()) in wanted for k, v in c["properties"].items())
        ]
        return FakeResponse(200, {"results": hits[:1]})

    def create(self, kwargs):
        record = {"id": str(len(self.companies) + 1), "properties": kwargs["json"]["properties"]}
        self.companies.append(record)
        return FakeResponse(201, record)


def http_with(crm):
    return FakeHttp(
        {
            ("POST", "/companies/search"): crm.search,
            ("POST", "/2026-09/companies"): crm.create,
        }
    )


def run(config, client, http, env=ENV, **kw):
    out, err = io.StringIO(), io.StringIO()
    sinks = build_sinks(config.delivery, env, http)
    code = run_leads(config, client, sinks, now=NOW, out=out, err=err, **kw)
    return code, out.getvalue(), err.getvalue()


def test_parser():
    parser = build_parser()
    args = parser.parse_args(["run", "--dry-run", "--max-new", "5", "-c", "x.yml"])
    assert (args.command, args.dry_run, args.max_new, args.config) == ("run", True, 5, "x.yml")
    assert parser.parse_args(["preview", "dentists in Leeds"]).query == "dentists in Leeds"
    assert parser.parse_args(["status"]).command == "status"
    assert parser.parse_args(["test-destinations", "--env-file", "x.env"]).env_file == "x.env"
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_first_run_delivers_and_second_run_sends_nothing(make_config, client):
    config = make_config()
    crm = Crm()
    http = http_with(crm)

    code, out, err = run(config, client, http, dry_run=False)
    assert code == 0, err
    assert "5 businesses found, 3 match your filters, 3 new" in out
    assert "Looking up websites: 3 Google searches" in out
    assert "API requests used: 5" in out
    assert "HubSpot: 3 created, 0 already there" in out
    assert [c["properties"]["name"] for c in crm.companies] == [
        "Kidd Roofing",
        "Dynamite Roofing & Construction",
        "Lapeyre Roofing",
    ]
    assert crm.companies[2]["properties"]["domain"] == "lapeyreroofing.com"
    slack = [r["json"]["text"] for r in http.sent("POST", "hooks.slack.test/x")]
    assert len(slack) == 1 and slack[0].startswith("*3 new leads from Google Maps*")
    hook = http.sent("POST", "example.test/hook")[0]["json"]
    assert [lead["name"] for lead in hook["leads"]] == [
        "Kidd Roofing",
        "Dynamite Roofing & Construction",
        "Lapeyre Roofing",
    ]
    saved = json.loads(Path(config.state).read_text(encoding="utf-8"))
    assert set(saved["leads"]) == {"tel:5126717791", "tel:5125058008", "tel:5128773087"}
    assert saved["leads"]["tel:5126717791"]["crm"] == {"hubspot": "created:1"}

    calls_before = len(client.calls)
    requests_before = len(http.requests)
    code, out, _ = run(config, client, http, dry_run=False)
    assert code == 0
    assert "3 match your filters, 0 new" in out
    assert "Nothing new to deliver." in out
    assert len(http.requests) == requests_before  # nothing sent anywhere
    assert [c[0] for c in client.calls[calls_before:]] == ["google_local", "google_local"]


def test_dry_run_delivers_and_saves_nothing(make_config, client):
    config = make_config()
    crm = Crm()
    http = http_with(crm)
    code, out, _ = run(config, client, http, dry_run=True)
    assert code == 0
    assert "Dry run: would deliver 3 new leads." in out
    assert (
        '--- HubSpot ---\nKidd Roofing: would create {"properties": {"name": "Kidd Roofing"' in out
    )
    assert "--- slack ---\n*3 new leads from Google Maps*" in out
    assert "Would add to HubSpot (3)" in out
    assert "Dry run: nothing was sent or saved." in out
    assert crm.companies == []
    assert {r["url"] for r in http.requests} == {
        "https://api.hubapi.com/crm/objects/2026-09/companies/search"
    }
    assert not Path(config.state).exists()


def test_leads_already_in_the_crm_are_remembered_not_posted(make_config, client):
    config = make_config()
    crm = Crm()
    crm.companies = [
        {"id": "77", "properties": {"name": "Kidd Roofing"}},
        {"id": "78", "properties": {"phone": "(512) 505-8008"}},
        {"id": "79", "properties": {"domain": "lapeyreroofing.com"}},
    ]
    http = http_with(crm)
    code, out, _ = run(config, client, http, dry_run=False)
    assert code == 0
    assert "HubSpot: 0 created, 3 already there" in out
    assert "Every new lead was already in your CRM; no digest to post." in out
    assert http.sent("POST", "hooks.slack.test/x") == []
    saved = json.loads(Path(config.state).read_text(encoding="utf-8"))
    assert saved["leads"]["tel:5126717791"]["crm"] == {"hubspot": "exists:77"}


def test_a_crm_that_rejects_everything_keeps_leads_for_next_run(make_config, client):
    config = make_config()
    http = FakeHttp({("POST", "/companies/search"): FakeResponse(401, {"message": "expired"})})
    code, out, err = run(config, client, http, dry_run=False)
    assert code == 1
    assert "hubspot: HTTP 401" in err
    assert "Not delivered: 3 leads. They are tried again next run." in out
    assert not Path(config.state).exists()


def test_without_any_destination_nothing_is_marked(make_config, client):
    config = make_config()
    env = {"SCRAPEUNBLOCKER_KEY": "k"}
    code, out, _ = run(config, client, FakeHttp(), env=env, dry_run=False)
    assert code == 0
    assert "no destination secret is set" in out
    assert "Kidd Roofing · Roofing contractor · 4.9★ (870 reviews)" in out
    assert not Path(config.state).exists()


def test_chat_only_delivery_marks_leads(make_config, client):
    config = make_config(delivery={"destinations": ["slack"]})
    http = FakeHttp()
    code, out, _ = run(config, client, http, dry_run=False)
    assert code == 0
    assert "Digest of 3 leads sent to slack." in out
    saved = json.loads(Path(config.state).read_text(encoding="utf-8"))
    assert len(saved["leads"]) == 3


def test_max_new_holds_the_rest_for_the_next_run(make_config, client):
    config = make_config(delivery={"destinations": ["slack"], "max_new_per_run": 1})
    http = FakeHttp()
    code, out, _ = run(config, client, http, dry_run=False)
    assert "3 new: 1 now, 2 on the next run" in out
    assert "2 more new leads wait for the next run" in http.requests[0]["json"]["text"]
    code, out, _ = run(config, client, http, dry_run=False, max_new=5)
    assert "3 match your filters, 2 new" in out


def test_website_lookup_can_be_switched_off(make_config, client):
    config = make_config(enrich={"website": False}, delivery={"destinations": ["webhook"]})
    code, out, _ = run(config, client, FakeHttp(), dry_run=False)
    assert "Looking up websites" not in out
    assert client.count("serp") == 0


def test_failed_search_is_reported(make_config, client):
    client.local["roofing contractors in Round Rock, TX"] = su.ServerError("502", status_code=502)
    config = make_config(delivery={"destinations": ["slack"]})
    code, out, _ = run(config, client, FakeHttp(), dry_run=False, annotate=True)
    assert code == 0  # one search failed, retried next run; the other worked
    assert "roofing contractors in Round Rock, TX" in out and "failed: ServerError" in out
    assert "::warning title=Search failed%3A roofing contractors in Round Rock%2C TX::" in out


def test_search_rotation():
    searches = [Search("dentists", w) for w in ("A", "B", "C", "D", "E")]
    assert pick_searches(searches, 0, 3) == (searches, 0)
    assert pick_searches(searches, 2, 0) == (searches[:2], 2)
    assert pick_searches(searches, 2, 4) == ([searches[4], searches[0]], 1)


def test_rotation_cursor_is_saved(make_config, client):
    config = make_config(
        searches={"per_run": 1}, delivery={"destinations": ["slack"]}, filters={"min_reviews": 0}
    )
    run(config, client, FakeHttp(), dry_run=False)
    assert json.loads(Path(config.state).read_text(encoding="utf-8"))["cursor"] == 1
    assert client.calls[0][1] == "roofing contractors in Austin, TX"
    run(config, client, FakeHttp(), dry_run=False)
    assert [c[1] for c in client.calls if c[0] == "google_local"][-1] == (
        "roofing contractors in Round Rock, TX"
    )


def write_config(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(
        'searches:\n  what: [roofing contractors]\n  where: ["Austin, TX"]\n'
        "filters:\n  min_reviews: 100\n"
        "delivery:\n  destinations: [slack]\n",
        encoding="utf-8",
    )
    return path


def test_main_needs_the_api_key(tmp_path):
    err = io.StringIO()
    code = main(
        ["run", "-c", str(write_config(tmp_path)), "--env-file", str(tmp_path / "none")],
        environ={},
        out=io.StringIO(),
        err=err,
    )
    assert code == 2 and "SCRAPEUNBLOCKER_KEY" in err.getvalue()


def test_main_run_and_status(tmp_path, client):
    path = write_config(tmp_path)
    http = FakeHttp()
    out = io.StringIO()
    env = {"SCRAPEUNBLOCKER_KEY": "k", "SLACK_WEBHOOK_URL": "https://hooks.slack.test/x"}
    code = main(
        ["run", "-c", str(path), "--env-file", str(tmp_path / "none")],
        environ=dict(env),
        client_factory=lambda e: client,
        http_factory=lambda: http,
        now=NOW,
        out=out,
        err=io.StringIO(),
    )
    assert code == 0, out.getvalue()
    assert "Saved leads.json." in out.getvalue()
    out = io.StringIO()
    assert main(["status", "-c", str(path)], environ={}, out=out, err=io.StringIO()) == 0
    assert "3 leads delivered, first on 2026-11-14" in out.getvalue()


def test_main_preview(tmp_path, client):
    out = io.StringIO()
    code = main(
        ["preview", "roofing contractors in Austin, TX", "-c", str(write_config(tmp_path))],
        environ={"SCRAPEUNBLOCKER_KEY": "k"},
        client_factory=lambda e: client,
        now=NOW,
        out=out,
        err=io.StringIO(),
    )
    text = out.getvalue()
    assert code == 0
    assert "Kidd Roofing" in text and "NEW" in text
    assert "fewer than 100 reviews" in text
    assert "4 businesses, 3 match your filters, 3 new. API requests used: 1." in text
    assert client.count("serp") == 0


def test_main_test_destinations(tmp_path):
    http = FakeHttp()
    out = io.StringIO()
    code = main(
        ["test-destinations", "-c", str(write_config(tmp_path))],
        environ={"SLACK_WEBHOOK_URL": "https://hooks.slack.test/x"},
        http_factory=lambda: http,
        out=out,
        err=io.StringIO(),
    )
    assert code == 0 and "slack     ok, test message sent" in out.getvalue()
    assert "connected" in http.requests[0]["json"]["text"]


def test_sinks_container():
    assert not Sinks([], [], {}).any
    assert Sinks([object()], [], {}).any


def test_fatal_api_error_stops_the_run(tmp_path):
    client = FakeClient(
        local={"roofing contractors in Austin, TX": su.AuthenticationError("bad", status_code=401)}
    )
    err = io.StringIO()
    code = main(
        ["run", "-c", str(write_config(tmp_path))],
        environ={"SCRAPEUNBLOCKER_KEY": "k"},
        client_factory=lambda e: client,
        http_factory=FakeHttp,
        now=NOW,
        out=io.StringIO(),
        err=err,
    )
    assert code == 2 and "AuthenticationError" in err.getvalue()
