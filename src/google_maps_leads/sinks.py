"""Destinations behind two small interfaces.

A **CRM** sink writes each lead as a record. It has ``name`` and ``label``, and
``find(lead)`` returns the id of a record that is already there (a read, safe in a dry
run), ``create(lead)`` writes a new record and returns its id, ``preview(lead)`` returns
the body ``create`` would send and ``test()`` checks the connection.

A **notify** sink posts the run digest. ``send(digest)`` delivers it, ``preview(digest)``
returns the message(s) it would post and ``test()`` sends a short hello.

Every method raises :class:`DeliveryError` on failure. Secrets come from environment
variables, never from ``config.yml``. To add a destination, write a class like the ones
below and register it in ``REGISTRY``.
"""

from __future__ import annotations

import html
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from .collect import Lead
from .config import AirtableSettings, Delivery, HubSpotSettings, PipedriveSettings
from .messages import DISCORD, PLAIN, SLACK, SOURCE, Digest, Style

USER_AGENT = (
    "google-maps-leads-to-crm (+https://github.com/ScrapeUnblocker/google-maps-leads-to-crm)"
)
CONNECTED = "google-maps-leads-to-crm is connected. New leads from Google Maps will arrive here."

HUBSPOT_API = "https://api.hubapi.com"
HUBSPOT_VERSION = "2026-09"  # HubSpot's date-versioned CRM API
PIPEDRIVE_API = "https://api.pipedrive.com"
AIRTABLE_API = "https://api.airtable.com"


class DeliveryError(RuntimeError):
    """A destination did not accept the request."""


class CrmSink(Protocol):
    name: str
    label: str
    warnings: list[str]

    def find(self, lead: Lead) -> str | None: ...

    def create(self, lead: Lead) -> str: ...

    def preview(self, lead: Lead) -> dict[str, Any]: ...

    def test(self) -> str: ...


class NotifySink(Protocol):
    name: str

    def send(self, digest: Digest) -> None: ...

    def preview(self, digest: Digest) -> list[str]: ...

    def test(self) -> str: ...


class Http(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> Any: ...


def request(
    http: Http,
    method: str,
    url: str,
    *,
    sink: str,
    sleep: Callable[[float], None] | None = None,
    **kwargs: Any,
) -> Any:
    """One HTTP call, retrying twice on 429/502/503/504. Errors never include the URL,
    because webhook URLs are secrets."""
    for attempt in (1, 2, 3):
        try:
            resp = http.request(method, url, timeout=30.0, **kwargs)
        except httpx.HTTPError as exc:
            raise DeliveryError(f"{sink}: request failed ({type(exc).__name__})") from None
        if resp.status_code in (429, 502, 503, 504) and attempt < 3:
            (sleep or time.sleep)(_retry_after(resp))
            continue
        if resp.status_code >= 300:
            body = " ".join((resp.text or "").split())[:300]
            raise DeliveryError(f"{sink}: HTTP {resp.status_code} {body}".rstrip())
        return resp
    raise AssertionError("unreachable")


def _retry_after(resp: Any) -> float:
    try:
        return min(float(resp.headers.get("Retry-After", "2")), 10.0)
    except (TypeError, ValueError, AttributeError):
        return 2.0


def _json(resp: Any, sink: str) -> Any:
    try:
        return resp.json()
    except ValueError:
        raise DeliveryError(f"{sink}: the answer was not JSON") from None


class Paced:
    """Keeps at least ``interval`` seconds between calls (CRM APIs allow ~5 per second)."""

    interval = 0.25

    def __init__(self, sleep: Callable[[float], None] | None = None) -> None:
        self.sleep = sleep
        self._last = 0.0

    def pace(self) -> None:
        wait = self.interval - (time.monotonic() - self._last)
        if wait > 0 and self._last:
            (self.sleep or time.sleep)(wait)
        self._last = time.monotonic()


def _text(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


# -- CRMs ---------------------------------------------------------------------------------


class HubSpotSink(Paced):
    """HubSpot companies (CRM API ``2026-09``) with a service key (or a private app token).

    A lead counts as already there when a company has the same phone, the same website
    domain or the same name.
    """

    name = "hubspot"
    label = "HubSpot"

    def __init__(
        self,
        token: str,
        http: Http,
        settings: HubSpotSettings,
        base_url: str = HUBSPOT_API,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        super().__init__(sleep)
        self.token = token
        self.http = http
        self.settings = settings
        self.base_url = base_url.rstrip("/")
        self.warnings: list[str] = []

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        self.pace()
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        resp = request(
            self.http,
            method,
            f"{self.base_url}/crm/objects/{HUBSPOT_VERSION}/companies{path}",
            sink=self.name,
            sleep=self.sleep,
            headers=headers,
            **kwargs,
        )
        return _json(resp, self.name)

    def find(self, lead: Lead) -> str | None:
        groups = []
        if lead.phone:
            groups.append({"propertyName": "phone", "operator": "EQ", "value": lead.phone})
        if lead.domain:
            groups.append({"propertyName": "domain", "operator": "EQ", "value": lead.domain})
        groups.append({"propertyName": "name", "operator": "EQ", "value": lead.name})
        body = {
            "filterGroups": [{"filters": [f]} for f in groups],
            "properties": ["name", "phone", "domain"],
            "limit": 1,
        }
        data = self._call("POST", "/search", json=body)
        results = data.get("results") if isinstance(data, dict) else None
        if results:
            return str(results[0].get("id") or "") or None
        return None

    def properties(self, lead: Lead) -> dict[str, str]:
        props = {}
        for prop, field_name in self.settings.properties.items():
            value = _text(lead.value(field_name))
            if value:
                props[prop] = value
        if self.settings.lifecycle_stage:
            props["lifecyclestage"] = self.settings.lifecycle_stage
        return props

    def preview(self, lead: Lead) -> dict[str, Any]:
        return {"properties": self.properties(lead)}

    def create(self, lead: Lead) -> str:
        data = self._call("POST", "", json=self.preview(lead))
        record = str(data.get("id") or "") if isinstance(data, dict) else ""
        if not record:
            raise DeliveryError("hubspot: the answer had no company id")
        return record

    def test(self) -> str:
        self._call("GET", "", params={"limit": 1, "properties": "name"})
        return "token works, companies are readable"


class PipedriveSink(Paced):
    """Pipedrive organizations (API v2) with a note holding the Google Maps facts, and a
    lead in the Leads Inbox. Uses a personal API token.

    A lead counts as already there when an organization has exactly the same name.
    """

    name = "pipedrive"
    label = "Pipedrive"

    def __init__(
        self,
        token: str,
        http: Http,
        settings: PipedriveSettings,
        base_url: str = PIPEDRIVE_API,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        super().__init__(sleep)
        self.token = token
        self.http = http
        self.settings = settings
        self.base_url = base_url.rstrip("/")
        self.warnings: list[str] = []

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        self.pace()
        resp = request(
            self.http,
            method,
            self.base_url + path,
            sink=self.name,
            sleep=self.sleep,
            headers={"x-api-token": self.token, "Accept": "application/json"},
            **kwargs,
        )
        data = _json(resp, self.name)
        if isinstance(data, dict) and data.get("success") is False:
            raise DeliveryError(f"pipedrive: {data.get('error') or 'request not accepted'}")
        return data

    def find(self, lead: Lead) -> str | None:
        params = {"term": lead.name[:100], "fields": "name", "exact_match": "true", "limit": 1}
        data = self._call("GET", "/api/v2/organizations/search", params=params)
        items = ((data or {}).get("data") or {}).get("items") or []
        for entry in items:
            item = entry.get("item") or {}
            if item.get("id") is not None:
                return str(item["id"])
        return None

    def organization(self, lead: Lead) -> dict[str, Any]:
        body: dict[str, Any] = {"name": lead.name}
        if lead.address:
            body["address"] = {"value": lead.address}
        if lead.website:
            body["website"] = lead.website
        custom = {}
        for key, field_name in self.settings.custom_fields.items():
            value = lead.value(field_name)
            if value not in (None, ""):
                custom[key] = value
        if custom:
            body["custom_fields"] = custom
        return body

    def note(self, lead: Lead) -> str:
        lines = [f"Phone: {lead.phone}"] if lead.phone else []
        lines += lead.summary().splitlines()
        return "<p>" + "<br>".join(html.escape(line) for line in lines) + "</p>"

    def preview(self, lead: Lead) -> dict[str, Any]:
        out: dict[str, Any] = {"organization": self.organization(lead)}
        if self.settings.note:
            out["note"] = self.note(lead)
        if self.settings.create_lead:
            out["lead"] = {"title": lead.name}
        return out

    def create(self, lead: Lead) -> str:
        data = self._call("POST", "/api/v2/organizations", json=self.organization(lead))
        org_id = ((data or {}).get("data") or {}).get("id")
        if org_id is None:
            raise DeliveryError("pipedrive: the answer had no organization id")
        # The organization exists now; a failed note or lead is reported, not retried.
        try:
            if self.settings.note:
                self._call("POST", "/v1/notes", json={"content": self.note(lead), "org_id": org_id})
            if self.settings.create_lead:
                self._call(
                    "POST", "/v1/leads", json={"title": lead.name, "organization_id": org_id}
                )
        except DeliveryError as exc:
            self.warnings.append(f"{lead.name}: organization {org_id} created, but {exc}")
        return str(org_id)

    def test(self) -> str:
        self._call("GET", "/api/v2/organizations", params={"limit": 1})
        return "token works, organizations are readable"


class AirtableSink(Paced):
    """An Airtable table, with a personal access token.

    Only the columns your table has are written (the table's columns are read with the
    ``schema.bases:read`` scope). When the table has the ``merge_on`` column (``Lead ID``),
    a lead whose id is already in it counts as already there.
    """

    name = "airtable"
    label = "Airtable"
    interval = 0.22

    def __init__(
        self,
        token: str,
        http: Http,
        settings: AirtableSettings,
        base: str,
        base_url: str = AIRTABLE_API,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        super().__init__(sleep)
        self.token = token
        self.http = http
        self.settings = settings
        self.base = base
        self.base_url = base_url.rstrip("/")
        self.warnings: list[str] = []
        self._schema: tuple[str, set[str]] | None = None
        self._schema_read = False

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        self.pace()
        resp = request(
            self.http,
            method,
            self.base_url + path,
            sink=self.name,
            sleep=self.sleep,
            headers={"Authorization": f"Bearer {self.token}"},
            **kwargs,
        )
        return _json(resp, self.name)

    def schema(self) -> tuple[str, set[str]] | None:
        """``(table id, column names)``, or None when the token cannot read the schema."""
        if self._schema_read:
            return self._schema
        try:
            data = self._call("GET", f"/v0/meta/bases/{self.base}/tables")
        except DeliveryError as exc:
            if "HTTP 403" not in str(exc):
                raise
            self.warnings.append(
                "the token cannot read the table's columns (add the schema.bases:read scope), "
                "so every mapped column is written"
            )
            self._schema_read = True
            return None
        wanted = self.settings.table
        tables = data.get("tables") if isinstance(data, dict) else None
        table = next(
            (t for t in tables or [] if wanted in (t.get("id"), t.get("name"))),
            None,
        ) or next(
            (t for t in tables or [] if str(t.get("name", "")).casefold() == wanted.casefold()),
            None,
        )
        if table is None:
            raise DeliveryError(
                f"airtable: no table named {wanted!r} in base {self.base}; create it first "
                "(see the README)"
            )
        columns = {str(f.get("name")) for f in table.get("fields") or []}
        missing = [c for c in self.settings.fields if c not in columns]
        if missing:
            self.warnings.append(
                f"table {table.get('name')!r} has no column {', '.join(missing)}; "
                "those values are skipped"
            )
        self._schema = (str(table.get("id")), columns)
        self._schema_read = True
        return self._schema

    def _table_path(self) -> str:
        schema = self.schema()
        table = schema[0] if schema else quote(self.settings.table, safe="")
        return f"/v0/{self.base}/{table}"

    def fields(self, lead: Lead) -> dict[str, Any]:
        schema = self.schema()
        out = {}
        for column, field_name in self.settings.fields.items():
            if schema and column not in schema[1]:
                continue
            value = lead.value(field_name)
            if value not in (None, ""):
                out[column] = value
        return out

    def find(self, lead: Lead) -> str | None:
        column = self.settings.merge_on
        if not column or column not in self.settings.fields:
            return None
        schema = self.schema()
        if schema and column not in schema[1]:
            return None
        value = str(lead.value(self.settings.fields[column]))
        literal = value.replace("\\", "\\\\").replace('"', '\\"')
        params = {"filterByFormula": f'{{{column}}}="{literal}"', "maxRecords": 1}
        data = self._call("GET", self._table_path(), params=params)
        records = data.get("records") if isinstance(data, dict) else None
        return str(records[0].get("id")) if records else None

    def preview(self, lead: Lead) -> dict[str, Any]:
        return {"records": [{"fields": self.fields(lead)}], "typecast": True}

    def create(self, lead: Lead) -> str:
        data = self._call("POST", self._table_path(), json=self.preview(lead))
        records = data.get("records") if isinstance(data, dict) else None
        if not records or not records[0].get("id"):
            raise DeliveryError("airtable: the answer had no record id")
        return str(records[0]["id"])

    def test(self) -> str:
        schema = self.schema()
        if schema is None:
            self._call("GET", self._table_path(), params={"maxRecords": 1})
            return f"token works, table {self.settings.table!r} is readable"
        writes = [c for c in self.settings.fields if c in schema[1]]
        return f"table {self.settings.table!r} found, {len(writes)} mapped column(s) present"


# -- chat and webhooks --------------------------------------------------------------------


class ChatSink:
    """A chat webhook that shows formatted text. Long digests are split over a few posts."""

    name = "chat"
    style: Style = PLAIN
    limit = 3900

    def __init__(self, webhook_url: str, http: Http) -> None:
        self.webhook_url = webhook_url
        self.http = http

    def preview(self, digest: Digest) -> list[str]:
        return digest.chunks(self.style, self.limit)

    def send(self, digest: Digest) -> None:
        for text in self.preview(digest):
            request(self.http, "POST", self.webhook_url, sink=self.name, json=self.wrap(text))

    def test(self) -> str:
        request(self.http, "POST", self.webhook_url, sink=self.name, json=self.wrap(CONNECTED))
        return "test message sent"

    def wrap(self, text: str) -> dict[str, Any]:
        raise NotImplementedError


class SlackSink(ChatSink):
    """Slack incoming webhook (mrkdwn). Link previews are off."""

    name = "slack"
    style = SLACK
    limit = 3900

    def wrap(self, text: str) -> dict[str, Any]:
        return {"text": text, "unfurl_links": False, "unfurl_media": False}


class DiscordSink(ChatSink):
    """Discord channel webhook. Link previews and mentions are off."""

    name = "discord"
    style = DISCORD
    limit = 1990

    def wrap(self, text: str) -> dict[str, Any]:
        return {"content": text, "allowed_mentions": {"parse": []}, "flags": 4}


class WebhookSink:
    """Any URL that accepts a JSON POST: Zapier, Make, n8n, Clay, your own endpoint.

    One POST per run with every new lead as JSON (see ``Digest.payload``).
    """

    name = "webhook"

    def __init__(self, url: str, http: Http) -> None:
        self.webhook_url = url
        self.http = http

    def preview(self, digest: Digest) -> list[str]:
        return [digest.render(PLAIN)]

    def send(self, digest: Digest) -> None:
        request(self.http, "POST", self.webhook_url, sink=self.name, json=digest.payload())

    def test(self) -> str:
        payload = {"source": SOURCE, "kind": "test", "text": CONNECTED}
        request(self.http, "POST", self.webhook_url, sink=self.name, json=payload)
        return "test message sent"


# -- registry -----------------------------------------------------------------------------


def _env(env: Mapping[str, str], name: str) -> str:
    return (env.get(name) or "").strip()


def _airtable_base(env: Mapping[str, str], delivery: Delivery) -> str:
    return _env(env, "AIRTABLE_BASE_ID") or delivery.airtable.base


@dataclass(frozen=True)
class SinkSpec:
    kind: str  # "crm" or "notify"
    env: tuple[str, ...]  # every one must be set for the destination to be used
    build: Callable[[Mapping[str, str], Http, Delivery], Any]
    extra: Callable[[Mapping[str, str], Delivery], str] | None = None  # another need, or ""

    def missing(self, env: Mapping[str, str], delivery: Delivery) -> str:
        """What is not set yet, or "" when the destination is ready."""
        need = [name for name in self.env if not _env(env, name)]
        if self.extra:
            other = self.extra(env, delivery)
            if other:
                need.append(other)
        return " and ".join(need)


REGISTRY: dict[str, SinkSpec] = {
    "hubspot": SinkSpec(
        "crm",
        ("HUBSPOT_TOKEN",),
        lambda env, http, d: HubSpotSink(
            _env(env, "HUBSPOT_TOKEN"), http, d.hubspot, _env(env, "HUBSPOT_API_URL") or HUBSPOT_API
        ),
    ),
    "pipedrive": SinkSpec(
        "crm",
        ("PIPEDRIVE_API_TOKEN",),
        lambda env, http, d: PipedriveSink(
            _env(env, "PIPEDRIVE_API_TOKEN"),
            http,
            d.pipedrive,
            _env(env, "PIPEDRIVE_API_URL") or PIPEDRIVE_API,
        ),
    ),
    "airtable": SinkSpec(
        "crm",
        ("AIRTABLE_TOKEN",),
        lambda env, http, d: AirtableSink(
            _env(env, "AIRTABLE_TOKEN"),
            http,
            d.airtable,
            _airtable_base(env, d),
            _env(env, "AIRTABLE_API_URL") or AIRTABLE_API,
        ),
        extra=lambda env, d: "" if _airtable_base(env, d) else "delivery.airtable.base",
    ),
    "slack": SinkSpec(
        "notify",
        ("SLACK_WEBHOOK_URL",),
        lambda env, http, d: SlackSink(_env(env, "SLACK_WEBHOOK_URL"), http),
    ),
    "discord": SinkSpec(
        "notify",
        ("DISCORD_WEBHOOK_URL",),
        lambda env, http, d: DiscordSink(_env(env, "DISCORD_WEBHOOK_URL"), http),
    ),
    "webhook": SinkSpec(
        "notify",
        ("LEADS_WEBHOOK_URL",),
        lambda env, http, d: WebhookSink(_env(env, "LEADS_WEBHOOK_URL"), http),
    ),
}


@dataclass
class Sinks:
    crm: list[Any]
    notify: list[Any]
    skipped: dict[str, str]  # name -> what is missing

    @property
    def any(self) -> bool:
        return bool(self.crm or self.notify)


def build_sinks(delivery: Delivery, env: Mapping[str, str], http: Http) -> Sinks:
    """Build every listed destination that is fully set up, in config order."""
    crm: list[Any] = []
    notify: list[Any] = []
    skipped: dict[str, str] = {}
    for name in delivery.destinations:
        spec = REGISTRY[name]
        missing = spec.missing(env, delivery)
        if missing:
            skipped[name] = missing
            continue
        sink = spec.build(env, http, delivery)
        (crm if spec.kind == "crm" else notify).append(sink)
    return Sinks(crm=crm, notify=notify, skipped=skipped)


def new_http_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=False)
