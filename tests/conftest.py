"""Shared fakes. No test calls the ScrapeUnblocker API or a real CRM: the SDK client and
the HTTP client are replaced by the small fakes below."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from google_maps_leads.config import parse_config

# Trimmed from a real Google Local answer ("roofing contractors in Austin, TX").
AUSTIN = [
    {
        "name": "Lapeyre Roofing My Ad Center",
        "sponsored": True,
        "rating": 5.0,
        "reviews": 348,
        "price": None,
        "category": "Roofing contractor",
        "address": "215 Brazos Street",
        "phone": "(512) 812-7501",
        "website": None,
        "hours": "Open 24 hours",
        "review_snippet": None,
        "position": 1,
    },
    {
        "name": "Kidd Roofing",
        "sponsored": False,
        "rating": 4.9,
        "reviews": 870,
        "price": None,
        "category": "Roofing contractor",
        "address": "40+ years in business · Austin, TX",
        "phone": "(512) 671-7791",
        "website": None,
        "hours": "Open · Closes 5 PM",
        "review_snippet": "Matt and his crew did a great job replacing our roof .",
        "position": 2,
    },
    {
        "name": "Lapeyre Roofing",
        "sponsored": False,
        "rating": 5.0,
        "reviews": 349,
        "price": None,
        "category": "Roofing contractor",
        "address": "3+ years in business · Austin, TX",
        "phone": "(512) 877-3087",
        "website": None,
        "hours": "Open 24 hours",
        "review_snippet": None,
        "position": 3,
    },
    {
        "name": "Austin Roofing Company LLC",
        "sponsored": False,
        "rating": 5.0,
        "reviews": 27,
        "price": None,
        "category": "Roofing contractor",
        "address": "15+ years in business · Austin, TX",
        "phone": "(512) 952-7454",
        "website": None,
        "hours": "Open · Closes 5 PM",
        "review_snippet": None,
        "position": 4,
    },
    {
        "name": "Texas Legacy Roofing",
        "sponsored": False,
        "rating": 3.9,
        "reviews": 150,
        "price": None,
        "category": "Roofing contractor",
        "address": "3+ years in business",
        "phone": "(512) 696-2035",
        "website": None,
        "hours": "Open · Closes 3 PM",
        "review_snippet": None,
        "position": 5,
    },
]

ROUND_ROCK = [
    {
        "name": "Dynamite Roofing & Construction",
        "sponsored": False,
        "rating": 4.9,
        "reviews": 362,
        "price": None,
        "category": "Roofing contractor",
        "address": "25+ years in business · Round Rock, TX",
        "phone": "(512) 505-8008",
        "website": None,
        "hours": "Open · Closes 6 PM",
        "review_snippet": None,
        "position": 1,
    },
    {
        # The same business as in the Austin search: same phone.
        "name": "Kidd Roofing",
        "sponsored": False,
        "rating": 4.9,
        "reviews": 870,
        "price": None,
        "category": "Roofing contractor",
        "address": "40+ years in business · Austin, TX",
        "phone": "(512) 671-7791",
        "website": None,
        "hours": "Open · Closes 5 PM",
        "review_snippet": None,
        "position": 2,
    },
]

# Organic results of a real Google search per business (trimmed).
SERP = {
    "Kidd Roofing": [
        {
            "title": "Kidd Roofing | Professional Roofing Company | Texas, CO, FL ...",
            "url": "https://www.kiddroof.com/",
            "description": "Contact us today at 866-671-7791 to talk with one of our roofing "
            "professionals.",
            "domain": "kiddroof.com",
        },
    ],
    "Lapeyre Roofing": [
        {
            "title": "Lapeyre Roofing",
            "url": "https://www.austinchamber.com/directory/lapeyre-roofing",
            "description": "Lapeyre Roofing. Location. 700 River St AUSTIN, Texas 78701. "
            "Website (512) 877-3087.",
            "domain": "austinchamber.com",
        },
        {
            "title": "LAPEYRE CONSTRUCTION - Reviews, Photos & Phone ...",
            "url": "https://lapeyre-construction.wheree.com/",
            "description": "Lapeyre Construction is a roofing contractor based in Austin, Texas",
            "domain": "lapeyre-construction.wheree.com",
        },
        {
            "title": "Roof installation in Lockhart, Texas",
            "url": "https://lapeyreroofing.com/roof-installation-lockhart-tx",
            "description": "Location Icon 215 Brazos St, Austin, TX 78701. Send us a message.",
            "domain": "lapeyreroofing.com",
        },
    ],
    "Dynamite Roofing & Construction": [
        {
            "title": "Dynamite Roofing & Construction | Yelp",
            "url": "https://www.yelp.com/biz/dynamite-roofing-and-construction-round-rock",
            "description": "Round Rock, TX (512) 505-8008",
            "domain": "yelp.com",
        },
        {
            "title": "Dynamite Roofing & Construction | Round Rock Roofer",
            "url": "https://dynamiteroof.com/",
            "description": "Roof repair and replacement in Round Rock and Austin.",
            "domain": "dynamiteroof.com",
        },
    ],
}


@dataclass
class FakeClient:
    """Stands in for ``scrapeunblocker.Client``: canned answers, recorded calls."""

    local: dict[str, Any] = field(default_factory=dict)  # query -> answer or exception
    serps: dict[str, Any] = field(default_factory=dict)  # business name -> organic list
    calls: list[tuple[str, str, dict]] = field(default_factory=list)

    def google_local(self, keyword: str, **kwargs: Any) -> Any:
        self.calls.append(("google_local", keyword, kwargs))
        answer = self.local.get(keyword, {"results": []})
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def serp(self, keyword: str, **kwargs: Any) -> Any:
        self.calls.append(("serp", keyword, kwargs))
        for name, organic in self.serps.items():
            if keyword.startswith(name):
                if isinstance(organic, BaseException):
                    raise organic
                return {"organic": organic, "pagesFetched": 1}
        return {"organic": [], "pagesFetched": 1}

    def count(self, method: str) -> int:
        return sum(1 for c in self.calls if c[0] == method)


class FakeResponse:
    def __init__(self, status: int = 200, data: Any = None, headers: dict | None = None):
        self.status_code = status
        self._data = data
        self.headers = headers or {}
        self.text = json.dumps(data) if data is not None else ""

    def json(self) -> Any:
        if self._data is None:
            raise ValueError("no JSON")
        return self._data


@dataclass
class FakeHttp:
    """Stands in for ``httpx.Client``. ``routes`` maps ``(method, url suffix)`` to a
    response or a callable ``(kwargs) -> response``; unrouted calls answer 200 ``{}``."""

    routes: dict[tuple[str, str], Any] = field(default_factory=dict)
    requests: list[dict[str, Any]] = field(default_factory=list)

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append({"method": method, "url": url, **kwargs})
        for (m, suffix), answer in self.routes.items():
            if m == method and url.split("?")[0].endswith(suffix):
                if isinstance(answer, list):
                    answer = answer.pop(0) if len(answer) > 1 else answer[0]
                return answer(kwargs) if callable(answer) else answer
        return FakeResponse(200, {})

    def sent(self, method: str, suffix: str) -> list[dict[str, Any]]:
        return [
            r
            for r in self.requests
            if r["method"] == method and r["url"].split("?")[0].endswith(suffix)
        ]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", slept.append)
    return slept


@pytest.fixture
def client() -> FakeClient:
    return FakeClient(
        local={
            "roofing contractors in Austin, TX": {"results": AUSTIN},
            "roofing contractors in Round Rock, TX": {"results": ROUND_ROCK},
        },
        serps=dict(SERP),
    )


@pytest.fixture
def make_config(tmp_path: Path) -> Callable[..., Any]:
    def make(**overrides: Any) -> Any:
        raw: dict[str, Any] = {
            "searches": {
                "what": ["roofing contractors"],
                "where": ["Austin, TX", "Round Rock, TX"],
                "country": "US",
            },
            "filters": {"min_rating": 4.0, "min_reviews": 100},
            "state": str(tmp_path / "state" / "leads.json"),
        }
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(raw.get(key), dict):
                raw[key] = {**raw[key], **value}
            else:
                raw[key] = value
        return parse_config(raw)

    return make
