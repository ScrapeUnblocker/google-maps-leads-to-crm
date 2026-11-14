"""The API calls: Google Maps searches and website lookups, with retry and shaping.

Businesses come from the ScrapeUnblocker Google Local endpoint (``POST /maps/google-local``,
up to about 20 businesses per search, one API request). A lead's website, which Google
Local rarely shows, is looked up with one Google search through serpApi
(``POST /serpApi``, one API request per lead) and matched in :mod:`.process`.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol, TypeVar
from urllib.parse import quote_plus, urlsplit

import scrapeunblocker as su

from .config import Search

T = TypeVar("T")

# Errors that a later attempt can fix. The SDK already retries 429/502/503/504 twice.
TRANSIENT: tuple[type[BaseException], ...] = (
    su.RateLimitError,
    su.ServerError,
    su.UpstreamOutageError,
    su.ScrapeTimeoutError,
    su.BrowserTimeoutError,
    su.BlockedError,
    su.ConnectionError,
)
# Errors that stop the whole run: a wrong key or no credit left.
FATAL: tuple[type[BaseException], ...] = (su.AuthenticationError, su.PaymentRequiredError)


class LeadClient(Protocol):
    """The two ``scrapeunblocker.Client`` methods this tool uses."""

    def google_local(
        self,
        keyword: str,
        *,
        proxy_country: str | None = None,
        hl: str | None = None,
        gl: str | None = None,
    ) -> Any: ...

    def serp(self, keyword: str, *, proxy_country: str | None = None) -> Any: ...


class FetchError(RuntimeError):
    """One search or lookup failed. ``permanent`` is False when retrying later can work."""

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


def call(
    fn: Callable[[], T],
    *,
    attempts: int = 2,
    pause: float = 5.0,
    sleep: Callable[[float], None] | None = None,
) -> T:
    """Run one API call, retrying transient failures once more after ``pause`` seconds."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except FATAL:
            raise
        except TRANSIENT as exc:
            if attempt == attempts:
                raise FetchError(f"{type(exc).__name__}: {exc}") from None
            (sleep or time.sleep)(pause)
        except su.ScrapeUnblockerError as exc:
            raise FetchError(f"{type(exc).__name__}: {exc}", permanent=True) from None
    raise AssertionError("unreachable")


# -- the lead record ----------------------------------------------------------------------

_AD_SUFFIX = re.compile(r"\s*My Ad Center\s*$")
_YEARS = re.compile(r"^(\d+)\+?\s+years?\s+in\s+business$", re.IGNORECASE)
_SPACES = re.compile(r"\s+")
_NOT_ALNUM = re.compile(r"[^0-9a-z]+")


def norm(text: str) -> str:
    """Lowercase words, punctuation collapsed: ``"Kidd Roofing, LLC"`` -> ``"kidd roofing llc"``."""
    return " ".join(_NOT_ALNUM.sub(" ", text.lower()).split())


def digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


@dataclass
class Lead:
    """One business from a Google Maps search, shaped for a CRM."""

    name: str
    search: str = ""  # the query it was found with, e.g. "roofing contractors in Austin, TX"
    where: str = ""  # the place part of that query
    category: str = ""
    rating: float | None = None
    reviews: int | None = None
    phone: str = ""
    street: str = ""
    locality: str = ""  # e.g. "Austin, TX", when Google shows one
    years_in_business: int | None = None
    open_24h: bool = False
    sponsored: bool = False  # runs Google Maps ads
    from_ad: bool = False  # only seen as an ad, never as a normal result
    price: str = ""
    website: str = ""
    website_source: str = ""  # "google_maps", "google_search" or "" (not found)
    website_checked: bool = False
    position: int | None = None
    other_phones: list[str] = field(default_factory=list)
    searches: list[str] = field(default_factory=list)
    first_seen: str = ""

    # identity -------------------------------------------------------------------------

    @property
    def phone_digits(self) -> str:
        return digits(self.phone)

    @property
    def name_key(self) -> str:
        return f"name:{norm(self.name)}@{norm(self.where or self.locality)}"

    @property
    def key(self) -> str:
        """The lead id in the state file and CRMs: ``tel:<digits>``, or a name key when
        Google shows no phone."""
        return f"tel:{self.phone_digits}" if self.phone_digits else self.name_key

    @property
    def identities(self) -> list[str]:
        """Every key that points at this business: its phones, its name in the searched
        place, and its bare name (``brand:``, or ``ad:`` when it was only seen as an ad)."""
        keys = [self.key]
        for phone in self.other_phones:
            d = digits(phone)
            if d and f"tel:{d}" not in keys:
                keys.append(f"tel:{d}")
        if self.name_key not in keys:
            keys.append(self.name_key)
        keys.append(f"{'ad' if self.from_ad else 'brand'}:{norm(self.name)}")
        return keys

    @property
    def lookups(self) -> list[str]:
        """The keys that find this business among others. A bare name only links an ad to
        a business: ads show call-tracking numbers and turn up in nearby towns' searches,
        but two normal results with one name in different towns are two businesses."""
        exact = self.identities[:-1]
        name = norm(self.name)
        return exact + ([f"ad:{name}", f"brand:{name}"] if self.from_ad else [f"ad:{name}"])

    # derived fields -------------------------------------------------------------------

    @property
    def domain(self) -> str:
        if not self.website:
            return ""
        host = urlsplit(self.website).hostname or ""
        return host.removeprefix("www.")

    @property
    def city(self) -> str:
        return self.locality.split(", ")[0] if self.locality else ""

    @property
    def region(self) -> str:
        parts = self.locality.split(", ")
        return parts[-1] if len(parts) > 1 else ""

    @property
    def address(self) -> str:
        return ", ".join(p for p in (self.street, self.locality) if p)

    @property
    def location(self) -> str:
        """The best short place label: the street, the town, or the searched place."""
        return self.address or self.where

    @property
    def maps_url(self) -> str:
        """A Google Maps link that opens this business (Maps URLs search form)."""
        place = self.address or self.where
        query = f"{self.name} {place}".strip()
        return f"https://www.google.com/maps/search/?api=1&query={quote_plus(query)}"

    def signals(self) -> list[str]:
        """Short facts a salesperson can open with."""
        out = []
        if self.sponsored:
            out.append("runs Google Maps ads")
        if self.years_in_business:
            out.append(f"{self.years_in_business}+ years in business")
        if self.open_24h:
            out.append("open 24 hours")
        if self.website_checked and not self.website:
            out.append("no website found")
        return out

    def rating_text(self) -> str:
        if self.rating is None:
            return "no rating"
        count = f" ({self.reviews:,} reviews)" if self.reviews is not None else ""
        return f"{self.rating:.1f}★{count}"

    def summary(self) -> str:
        """A plain-text note for the CRM record's description."""
        first = " · ".join(
            p for p in (self.category, self.rating_text() if self.rating is not None else "") if p
        )
        lines = [first] if first else []
        found = f'Found on Google Maps for "{self.search}"'
        if self.first_seen:
            found += f" on {self.first_seen}"
        lines.append(found)
        signals = self.signals()
        if signals:
            lines.append(", ".join(s[:1].upper() + s[1:] for s in signals))
        lines.append(f"Google Maps: {self.maps_url}")
        return "\n".join(lines)

    def value(self, name: str) -> Any:
        """A field by its config.yml name (see ``config.LEAD_FIELDS``)."""
        if name == "signals":
            return ", ".join(self.signals())
        if name in ("rating", "reviews", "years_in_business", "sponsored"):
            return getattr(self, name)
        if name == "summary":
            return self.summary()
        return getattr(self, name) or ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "category": self.category or None,
            "rating": self.rating,
            "reviews": self.reviews,
            "phone": self.phone or None,
            "website": self.website or None,
            "website_source": self.website_source or None,
            "street": self.street or None,
            "locality": self.locality or None,
            "years_in_business": self.years_in_business,
            "sponsored": self.sponsored,
            "signals": self.signals(),
            "search": self.search,
            "maps_url": self.maps_url,
            "first_seen": self.first_seen or None,
        }


# -- shaping ------------------------------------------------------------------------------


def clean_name(name: str) -> str:
    """Drop the "My Ad Center" label that sponsored results carry."""
    return _SPACES.sub(" ", _AD_SUFFIX.sub("", name)).strip()


def parse_address(text: str) -> tuple[str, str, int | None]:
    """Split Google Local's address line into ``(street, locality, years_in_business)``.

    It holds a street (``"2121 S Oneida St #321"``), or facts joined by `` · ``
    (``"10+ years in business · Austin, TX"``), or both.
    """
    street = locality = ""
    years: int | None = None
    for part in (p.strip() for p in (text or "").split("·")):
        if not part:
            continue
        match = _YEARS.match(part)
        if match:
            years = int(match.group(1))
        elif any(ch.isdigit() for ch in part):
            street = street or part
        else:
            locality = locality or part
    return street, locality, years


def _number(value: Any, kind: type) -> Any:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return kind(value)
    except (TypeError, ValueError):
        return None


def lead_from_result(result: Any, search: Search, today: date | None = None) -> Lead | None:
    """Shape one Google Local result. Returns None for a row without a name."""
    if not isinstance(result, dict):
        return None
    name = clean_name(str(result.get("name") or ""))
    if not name:
        return None
    street, locality, years = parse_address(str(result.get("address") or ""))
    hours = str(result.get("hours") or "")
    website = str(result.get("website") or "").strip()
    if website and not website.startswith(("http://", "https://")):
        website = "https://" + website
    return Lead(
        name=name,
        search=search.query,
        where=search.where,
        category=str(result.get("category") or "").strip(),
        rating=_number(result.get("rating"), float),
        reviews=_number(result.get("reviews"), int),
        phone=str(result.get("phone") or "").strip(),
        street=street,
        locality=locality,
        years_in_business=years,
        open_24h="open 24 hours" in hours.lower(),
        sponsored=bool(result.get("sponsored")),
        from_ad=bool(result.get("sponsored")),
        price=str(result.get("price") or "").strip(),
        website=website,
        website_source="google_maps" if website else "",
        website_checked=bool(website),
        position=_number(result.get("position"), int),
        searches=[search.query],
        first_seen=today.isoformat() if today else "",
    )


# -- the calls ----------------------------------------------------------------------------


def search_businesses(
    client: LeadClient,
    search: Search,
    *,
    country: str,
    language: str,
    today: date | None = None,
    sleep: Callable[[float], None] | None = None,
) -> list[Lead]:
    """One Google Maps search (one API request): up to about 20 businesses."""

    def fetch() -> Any:
        return client.google_local(
            search.query, proxy_country=country, gl=country.lower(), hl=language
        )

    raw = call(fetch, sleep=sleep)
    results = raw.get("results") if isinstance(raw, dict) else None
    if not isinstance(results, list):
        raise FetchError("unexpected answer: no results list", permanent=True)
    leads = [lead_from_result(r, search, today) for r in results]
    return [lead for lead in leads if lead is not None]


def website_query(lead: Lead) -> str:
    """The Google search that finds a business's own site: its name and where it is."""
    return f"{lead.name} {lead.street or lead.locality or lead.where}".strip()


def search_web(
    client: LeadClient,
    query: str,
    *,
    country: str,
    sleep: Callable[[float], None] | None = None,
) -> list[dict[str, Any]]:
    """One Google search (one API request): the organic results."""
    raw = call(lambda: client.serp(query, proxy_country=country), sleep=sleep)
    organic = raw.get("organic") if isinstance(raw, dict) else None
    return [r for r in organic if isinstance(r, dict)] if isinstance(organic, list) else []
