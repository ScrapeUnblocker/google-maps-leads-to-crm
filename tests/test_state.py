import json

import pytest

from google_maps_leads.collect import Lead
from google_maps_leads.state import State, StateError


def lead(**kw):
    base = {"name": "Kidd Roofing", "phone": "(512) 671-7791", "where": "Austin, TX"}
    return Lead(**{**base, **kw})


def test_add_save_load_round_trip(tmp_path):
    path = tmp_path / "state" / "leads.json"
    state = State()
    state.add(lead(other_phones=["(512) 000-1111"]), {"hubspot": "created:42"}, "2026-11-14")
    state.cursor = 2
    assert state.save(path)
    assert not state.save(path)  # unchanged: not written again

    loaded = State.load(path)
    assert loaded.cursor == 2
    record = loaded.leads["tel:5126717791"]
    assert record.name == "Kidd Roofing" and record.first_seen == "2026-11-14"
    assert record.crm == {"hubspot": "created:42"}
    assert record.ids == ["tel:5120001111", "name:kidd roofing@austin tx", "brand:kidd roofing"]
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_is_known_by_any_phone_or_name():
    state = State()
    state.add(lead(other_phones=["(512) 000-1111"]), {}, "2026-11-14")
    assert state.is_known(lead())
    assert state.is_known(lead(phone="(512) 000-1111"))  # the other number
    assert state.is_known(lead(phone=""))  # same name, same town
    assert not state.is_known(lead(phone="(303) 555-0100", where="Denver, CO"))
    # An ad with the name is the same business, wherever it shows up.
    assert state.is_known(lead(phone="(303) 555-0100", where="Denver, CO", from_ad=True))


def test_an_empty_state_is_not_written(tmp_path):
    path = tmp_path / "leads.json"
    assert not State().save(path)
    assert not path.exists()


@pytest.mark.parametrize(
    "text",
    ["not json", "[]", '{"leads": []}', '{"leads": {"x": 1}}', '{"cursor": -1, "leads": {}}'],
)
def test_bad_state_file(tmp_path, text):
    path = tmp_path / "leads.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(StateError):
        State.load(path)
