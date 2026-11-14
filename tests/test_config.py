from pathlib import Path

import pytest

from google_maps_leads.config import (
    DESTINATIONS,
    ConfigError,
    Search,
    load_config,
    parse_config,
)

ROOT = Path(__file__).resolve().parents[1]


def test_shipped_config_loads():
    config = load_config(ROOT / "config.yml")
    assert [s.query for s in config.searches.all()] == [
        "roofing contractors in Austin, TX",
        "roofing contractors in Round Rock, TX",
        "roofing contractors in Cedar Park, TX",
    ]
    assert config.searches.country == "US" and config.searches.per_run == 0
    assert config.filters.min_rating == 4.0 and config.filters.min_reviews == 10
    assert config.enrich.website
    assert config.delivery.destinations == DESTINATIONS
    assert config.delivery.max_new_per_run == 25
    assert config.delivery.hubspot.properties["description"] == "summary"
    assert config.delivery.airtable.fields["Lead ID"] == "key"
    assert config.state == ROOT / "state" / "leads.json"


def test_minimal_config_uses_defaults():
    config = parse_config({"searches": {"what": "dentists", "country": "gb"}})
    assert config.searches.all() == [Search("dentists")]
    assert config.searches.country == "GB"
    assert config.filters.require_phone
    assert config.delivery.hubspot.lifecycle_stage == "lead"
    assert config.delivery.pipedrive.note and config.delivery.pipedrive.create_lead


def test_every_what_in_every_where():
    config = parse_config(
        {"searches": {"what": ["dentists", "orthodontists"], "where": ["Leeds", "York"]}}
    )
    assert [s.query for s in config.searches.all()] == [
        "dentists in Leeds",
        "dentists in York",
        "orthodontists in Leeds",
        "orthodontists in York",
    ]


def test_field_maps_can_add_and_drop_fields():
    config = parse_config(
        {
            "searches": {"what": "x"},
            "delivery": {
                "hubspot": {"properties": {"name": "name", "google_rating": "rating"}},
                "airtable": {"fields": {"Name": "name", "Phone": ""}},
            },
        }
    )
    assert config.delivery.hubspot.properties == {"name": "name", "google_rating": "rating"}
    assert config.delivery.airtable.fields == {"Name": "name"}


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({}, "add a searches: section"),
        ({"searches": {}}, "searches.what"),
        ({"searches": {"what": "x", "country": "USA"}}, "2-letter"),
        ({"searches": {"what": "x", "language": "English"}}, "searches.language"),
        ({"searches": {"what": "x", "per_run": -1}}, "searches.per_run"),
        ({"searches": {"what": "x", "colour": "red"}}, "unknown key"),
        ({"searches": {"what": "x"}, "extra": 1}, "unknown key"),
        ({"searches": {"what": "x"}, "filters": {"min_rating": 6}}, "min_rating"),
        (
            {"searches": {"what": "x"}, "filters": {"min_reviews": 50, "max_reviews": 10}},
            "max_reviews",
        ),
        ({"searches": {"what": "x"}, "filters": {"sponsored": "maybe"}}, "sponsored"),
        ({"searches": {"what": "x"}, "filters": {"require_phone": "yes"}}, "true or false"),
        ({"searches": {"what": "x"}, "delivery": {"destinations": ["teams"]}}, "unknown dest"),
        ({"searches": {"what": "x"}, "delivery": {"destinations": []}}, "at least one"),
        ({"searches": {"what": "x"}, "delivery": {"max_new_per_run": 0}}, "max_new_per_run"),
        (
            {"searches": {"what": "x"}, "delivery": {"hubspot": {"properties": {"x": "ratin"}}}},
            "not a lead field",
        ),
        (
            {"searches": {"what": "x"}, "delivery": {"hubspot": {"properties": {"Name": "name"}}}},
            "internal property",
        ),
        (
            {
                "searches": {"what": "x"},
                "delivery": {"hubspot": {"properties": {"phone": "phone"}}},
            },
            "keep name",
        ),
        ({"searches": {"what": "x"}, "delivery": {"airtable": {"base": "abc"}}}, "base id"),
        ({"searches": {"what": "x"}, "delivery": {"airtable": {"fields": {}}}}, "map at least"),
    ],
)
def test_config_errors(raw, message):
    with pytest.raises(ConfigError) as info:
        parse_config(raw)
    assert message in str(info.value)


def test_load_config_errors(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.yml")
    bad = tmp_path / "bad.yml"
    bad.write_text("searches: [", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(bad)
    empty = tmp_path / "empty.yml"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ConfigError, match="empty"):
        load_config(empty)
