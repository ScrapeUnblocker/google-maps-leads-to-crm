"""Load and check ``config.yml``, the one file a user edits.

Secrets never live here: the API key, CRM tokens and webhook URLs come from environment
variables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from typing import Any

import yaml

CRMS = ("hubspot", "pipedrive", "airtable")
NOTIFY = ("slack", "discord", "webhook")
DESTINATIONS = CRMS + NOTIFY
SPONSORED = ("include", "exclude", "only")

# Lead fields that the CRM field maps in config.yml can point at.
LEAD_FIELDS = (
    "name",
    "category",
    "rating",
    "reviews",
    "phone",
    "website",
    "domain",
    "address",
    "street",
    "city",
    "region",
    "years_in_business",
    "signals",
    "sponsored",
    "search",
    "maps_url",
    "summary",
    "key",
    "first_seen",
)

_COUNTRY = re.compile(r"^[A-Za-z]{2}$")
_LANGUAGE = re.compile(r"^[a-z]{2}(-[A-Za-z]{2})?$")
_HUBSPOT_PROPERTY = re.compile(r"^[a-z0-9_]+$")


class ConfigError(ValueError):
    """config.yml is missing, unreadable or has a value that cannot work."""


@dataclass(frozen=True)
class Search:
    """One Google Maps search: a kind of business in a place."""

    what: str
    where: str = ""

    @property
    def query(self) -> str:
        return f"{self.what} in {self.where}" if self.where else self.what

    def __str__(self) -> str:
        return self.query


@dataclass(frozen=True)
class Searches:
    what: tuple[str, ...]
    where: tuple[str, ...] = ()
    country: str = "US"
    language: str = "en"
    per_run: int = 0  # 0 = every search on every run

    def all(self) -> list[Search]:
        """Every ``what`` in every ``where``, in config order."""
        if not self.where:
            return [Search(w) for w in self.what]
        return [Search(w, place) for w, place in product(self.what, self.where)]


@dataclass(frozen=True)
class Filters:
    min_rating: float = 0.0
    min_reviews: int = 0
    max_reviews: int = 0  # 0 = no upper limit
    require_phone: bool = True
    categories: tuple[str, ...] = ()
    exclude_categories: tuple[str, ...] = ()
    exclude_names: tuple[str, ...] = ()
    sponsored: str = "include"


@dataclass(frozen=True)
class Enrich:
    website: bool = True


@dataclass(frozen=True)
class HubSpotSettings:
    properties: dict[str, str] = field(
        default_factory=lambda: {
            "name": "name",
            "phone": "phone",
            "domain": "domain",
            "website": "website",
            "address": "street",
            "city": "city",
            "state": "region",
            "description": "summary",
        }
    )
    lifecycle_stage: str = "lead"


@dataclass(frozen=True)
class PipedriveSettings:
    note: bool = True
    create_lead: bool = True
    custom_fields: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AirtableSettings:
    base: str = ""
    table: str = "Leads"
    merge_on: str = "Lead ID"
    fields: dict[str, str] = field(
        default_factory=lambda: {
            "Name": "name",
            "Category": "category",
            "Rating": "rating",
            "Reviews": "reviews",
            "Phone": "phone",
            "Website": "website",
            "Address": "address",
            "Signals": "signals",
            "Search": "search",
            "Google Maps": "maps_url",
            "First seen": "first_seen",
            "Lead ID": "key",
        }
    )


@dataclass(frozen=True)
class Delivery:
    destinations: tuple[str, ...] = DESTINATIONS
    max_new_per_run: int = 25
    hubspot: HubSpotSettings = field(default_factory=HubSpotSettings)
    pipedrive: PipedriveSettings = field(default_factory=PipedriveSettings)
    airtable: AirtableSettings = field(default_factory=AirtableSettings)


@dataclass(frozen=True)
class Config:
    searches: Searches
    filters: Filters = field(default_factory=Filters)
    enrich: Enrich = field(default_factory=Enrich)
    delivery: Delivery = field(default_factory=Delivery)
    state: Path = Path("state/leads.json")


# -- parsing helpers ----------------------------------------------------------------------


def _section(raw: Any, name: str, allowed: tuple[str, ...]) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{name}: expected a mapping")
    unknown = sorted(str(k) for k in raw if k not in allowed)
    if unknown:
        raise ConfigError(f"{name}: unknown key(s) {', '.join(unknown)}")
    return raw


def _strings(raw: Any, name: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise ConfigError(f"{name}: expected a list of strings")
    out = []
    for item in raw:
        if not isinstance(item, (str, int, float)) or isinstance(item, bool):
            raise ConfigError(f"{name}: expected a list of strings")
        text = " ".join(str(item).split())
        if text:
            out.append(text)
    return tuple(dict.fromkeys(out))


def _bool(raw: Any, name: str, default: bool) -> bool:
    if raw is None:
        return default
    if not isinstance(raw, bool):
        raise ConfigError(f"{name}: expected true or false")
    return raw


def _int(raw: Any, name: str, default: int, minimum: int = 0) -> int:
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < minimum:
        raise ConfigError(f"{name}: expected a whole number >= {minimum}")
    return raw


def _number(raw: Any, name: str, default: float, maximum: float) -> float:
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not 0 <= raw <= maximum:
        raise ConfigError(f"{name}: expected a number from 0 to {maximum:g}")
    return float(raw)


def _field_map(raw: Any, name: str, default: dict[str, str]) -> dict[str, str]:
    if raw is None:
        return dict(default)
    if not isinstance(raw, dict):
        raise ConfigError(f"{name}: expected a mapping of <CRM field>: <lead field>")
    out: dict[str, str] = {}
    for key, value in raw.items():
        if value in (None, ""):
            continue  # an empty value switches a default field off
        if not isinstance(value, str) or value not in LEAD_FIELDS:
            raise ConfigError(
                f"{name}.{key}: {value!r} is not a lead field. Use one of: {', '.join(LEAD_FIELDS)}"
            )
        out[str(key)] = value
    return out


# -- sections -----------------------------------------------------------------------------


def _searches(raw: Any) -> Searches:
    raw = _section(raw, "searches", ("what", "where", "country", "language", "per_run"))
    what = _strings(raw.get("what"), "searches.what")
    if not what:
        raise ConfigError('searches.what: add at least one kind of business, e.g. "dentists"')
    where = _strings(raw.get("where"), "searches.where")
    country = str(raw.get("country") or "US").strip()
    if not _COUNTRY.match(country):
        raise ConfigError(f"searches.country: expected a 2-letter country code, got {country!r}")
    language = str(raw.get("language") or "en").strip()
    if not _LANGUAGE.match(language):
        raise ConfigError(f"searches.language: expected a language code like en, got {language!r}")
    return Searches(
        what=what,
        where=where,
        country=country.upper(),
        language=language,
        per_run=_int(raw.get("per_run"), "searches.per_run", 0),
    )


def _filters(raw: Any) -> Filters:
    raw = _section(
        raw,
        "filters",
        (
            "min_rating",
            "min_reviews",
            "max_reviews",
            "require_phone",
            "categories",
            "exclude_categories",
            "exclude_names",
            "sponsored",
        ),
    )
    min_reviews = _int(raw.get("min_reviews"), "filters.min_reviews", 0)
    max_reviews = _int(raw.get("max_reviews"), "filters.max_reviews", 0)
    if max_reviews and max_reviews < min_reviews:
        raise ConfigError("filters.max_reviews: must be 0 (no limit) or >= min_reviews")
    sponsored = str(raw.get("sponsored") or "include").strip().lower()
    if sponsored not in SPONSORED:
        raise ConfigError(f"filters.sponsored: expected one of {', '.join(SPONSORED)}")
    return Filters(
        min_rating=_number(raw.get("min_rating"), "filters.min_rating", 0.0, 5.0),
        min_reviews=min_reviews,
        max_reviews=max_reviews,
        require_phone=_bool(raw.get("require_phone"), "filters.require_phone", True),
        categories=_strings(raw.get("categories"), "filters.categories"),
        exclude_categories=_strings(raw.get("exclude_categories"), "filters.exclude_categories"),
        exclude_names=_strings(raw.get("exclude_names"), "filters.exclude_names"),
        sponsored=sponsored,
    )


def _delivery(raw: Any) -> Delivery:
    raw = _section(
        raw,
        "delivery",
        ("destinations", "max_new_per_run", "hubspot", "pipedrive", "airtable"),
    )
    destinations = _strings(raw.get("destinations"), "delivery.destinations")
    if raw.get("destinations") is None:
        destinations = DESTINATIONS
    for name in destinations:
        if name not in DESTINATIONS:
            raise ConfigError(
                f"delivery.destinations: unknown destination {name!r}. "
                f"Use any of: {', '.join(DESTINATIONS)}"
            )
    if not destinations:
        raise ConfigError("delivery.destinations: list at least one destination")

    hs_raw = _section(raw.get("hubspot"), "delivery.hubspot", ("properties", "lifecycle_stage"))
    hs_default = HubSpotSettings()
    properties = _field_map(
        hs_raw.get("properties"), "delivery.hubspot.properties", hs_default.properties
    )
    for prop in properties:
        if not _HUBSPOT_PROPERTY.match(prop):
            raise ConfigError(
                f"delivery.hubspot.properties: {prop!r} is not a HubSpot internal property "
                "name (lowercase, digits and _)"
            )
    if "name" not in properties:
        raise ConfigError("delivery.hubspot.properties: keep name: name")
    lifecycle = hs_raw.get("lifecycle_stage", hs_default.lifecycle_stage)
    hubspot = HubSpotSettings(properties=properties, lifecycle_stage=str(lifecycle or "").strip())

    pd_raw = _section(
        raw.get("pipedrive"), "delivery.pipedrive", ("note", "create_lead", "custom_fields")
    )
    pipedrive = PipedriveSettings(
        note=_bool(pd_raw.get("note"), "delivery.pipedrive.note", True),
        create_lead=_bool(pd_raw.get("create_lead"), "delivery.pipedrive.create_lead", True),
        custom_fields=_field_map(
            pd_raw.get("custom_fields"), "delivery.pipedrive.custom_fields", {}
        ),
    )

    at_raw = _section(
        raw.get("airtable"), "delivery.airtable", ("base", "table", "merge_on", "fields")
    )
    at_default = AirtableSettings()
    base = str(at_raw.get("base") or "").strip()
    if base and not re.match(r"^app[A-Za-z0-9]{14}$", base):
        raise ConfigError(
            f"delivery.airtable.base: expected a base id like appXXXXXXXXXXXXXX, got {base!r}"
        )
    fields = _field_map(at_raw.get("fields"), "delivery.airtable.fields", at_default.fields)
    if not fields:
        raise ConfigError("delivery.airtable.fields: map at least one column")
    airtable = AirtableSettings(
        base=base,
        table=str(at_raw.get("table") or at_default.table).strip(),
        merge_on=str(at_raw.get("merge_on", at_default.merge_on) or "").strip(),
        fields=fields,
    )
    return Delivery(
        destinations=destinations,
        max_new_per_run=_int(raw.get("max_new_per_run"), "delivery.max_new_per_run", 25, 1),
        hubspot=hubspot,
        pipedrive=pipedrive,
        airtable=airtable,
    )


def parse_config(raw: Any, base_dir: Path | None = None) -> Config:
    raw = _section(raw, "config.yml", ("searches", "filters", "enrich", "delivery", "state"))
    if "searches" not in raw:
        raise ConfigError("config.yml: add a searches: section (what to look for, and where)")
    enrich_raw = _section(raw.get("enrich"), "enrich", ("website",))
    state = Path(str(raw.get("state") or "state/leads.json"))
    if base_dir is not None and not state.is_absolute():
        state = base_dir / state
    return Config(
        searches=_searches(raw.get("searches")),
        filters=_filters(raw.get("filters")),
        enrich=Enrich(website=_bool(enrich_raw.get("website"), "enrich.website", True)),
        delivery=_delivery(raw.get("delivery")),
        state=state,
    )


def load_config(path: str | Path) -> Config:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"{path} not found (run from the repo folder or pass -c)") from None
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from None
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from None
    if raw is None:
        raise ConfigError(f"{path} is empty")
    return parse_config(raw, base_dir=path.resolve().parent)
