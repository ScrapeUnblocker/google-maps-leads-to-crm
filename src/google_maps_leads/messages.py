"""The run digest: one message listing the new leads, for Slack, Discord and webhooks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .collect import Lead

SOURCE = "google-maps-leads-to-crm"


@dataclass(frozen=True)
class Style:
    name: str
    bold: Callable[[str], str]
    link: Callable[[str, str], str]  # (url, text)


def _escape_slack(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_discord(text: str) -> str:
    for ch in "\\*_~`|[]":
        text = text.replace(ch, "\\" + ch)
    return text


PLAIN = Style("plain", bold=lambda t: t, link=lambda url, text: url)
SLACK = Style(
    "slack",
    bold=lambda t: f"*{_escape_slack(t)}*",
    link=lambda url, text: f"<{url}|{_escape_slack(text)}>",
)
DISCORD = Style(
    "discord",
    bold=lambda t: f"**{_escape_discord(t)}**",
    link=lambda url, text: f"[{_escape_discord(text)}](<{url}>)",
)


@dataclass(frozen=True)
class Outcome:
    """What one CRM did with one lead."""

    status: str  # "created", "exists" or "would_create" (dry run)
    record_id: str = ""

    def to_state(self) -> str:
        return f"{self.status}:{self.record_id}" if self.record_id else self.status


@dataclass
class Delivered:
    lead: Lead
    crm: dict[str, Outcome] = field(default_factory=dict)  # sink name -> outcome

    @property
    def is_new(self) -> bool:
        """New to at least one CRM (or there is no CRM, only chat)."""
        return not self.crm or any(o.status != "exists" for o in self.crm.values())


def plural(n: int, word: str, many: str = "") -> str:
    return f"{n} {word}" if n == 1 else f"{n} {many or word + 's'}"


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


@dataclass
class Digest:
    """Everything one run delivers."""

    day: str
    searches: list[str]
    leads: list[Delivered]
    crm_labels: dict[str, str] = field(default_factory=dict)  # sink name -> "HubSpot"
    waiting: int = 0  # new leads held back by delivery.max_new_per_run

    @property
    def listed(self) -> list[Delivered]:
        return [d for d in self.leads if d.is_new]

    @property
    def subject(self) -> str:
        return f"{plural(len(self.listed), 'new lead')} from Google Maps"

    def searches_text(self) -> str:
        if not self.searches:
            return ""
        if len(self.searches) == 1:
            return self.searches[0]
        more = plural(len(self.searches) - 1, "more search", "more searches")
        return f"{self.searches[0]} and {more}"

    def crm_line(self) -> str:
        """``Added to HubSpot (11) and Airtable (12) · 1 already in HubSpot``."""
        if not self.crm_labels:
            return ""
        added, already, verb = [], [], "Added to"
        for name, label in self.crm_labels.items():
            outcomes = [d.crm[name] for d in self.leads if name in d.crm]
            new = sum(1 for o in outcomes if o.status in ("created", "would_create"))
            if any(o.status == "would_create" for o in outcomes):
                verb = "Would add to"
            old = sum(1 for o in outcomes if o.status == "exists")
            if new:
                added.append(f"{label} ({new})")
            if old:
                already.append(f"{old} already in {label}")
        parts = [f"{verb} {_join(added)}"] if added else []
        return " · ".join(parts + already)

    def lead_block(self, item: Delivered, style: Style) -> str:
        lead = item.lead
        head = [style.bold(lead.name)]
        if lead.category:
            head.append(lead.category)
        head.append(lead.rating_text())
        contact = [p for p in (lead.phone,) if p]
        if lead.website:
            contact.append(
                style.link(lead.website, lead.domain) if style is not PLAIN else lead.website
            )
        if lead.location:
            contact.append(lead.location)
        if style is not PLAIN:
            contact.append(style.link(lead.maps_url, "Maps"))
        lines = [" · ".join(head), " · ".join(contact)]
        facts = list(lead.signals())
        existing = [
            self.crm_labels.get(name, name) for name, o in item.crm.items() if o.status == "exists"
        ]
        if existing:
            facts.append(f"already in {_join(existing)}")
        if facts:
            lines.append(" · ".join(facts))
        if style is PLAIN:
            lines.append(f"Maps: {lead.maps_url}")
        return "\n".join(line for line in lines if line)

    def chunks(self, style: Style, limit: int = 100_000, max_posts: int = 5) -> list[str]:
        """The digest as one or more messages of at most ``limit`` characters (Slack and
        Discord cap a post). After ``max_posts`` messages the rest is summed up."""
        where = self.searches_text()
        lines = [style.bold(self.subject) + (f" · {where}" if where else "")]
        crm = self.crm_line()
        if crm:
            lines.append(crm)
        if self.waiting:
            lines.append(
                f"{plural(self.waiting, 'more new lead')} wait for the next run "
                "(delivery.max_new_per_run)."
            )
        posts: list[str] = []
        current = "\n".join(lines)
        budget = limit - 40  # room for the "…and N more" line
        blocks = [self.lead_block(item, style)[:budget] for item in self.listed]
        for i, block in enumerate(blocks):
            if len(current) + 2 + len(block) <= budget:
                current += "\n\n" + block
                continue
            if len(posts) + 1 >= max_posts:
                current += f"\n\n…and {len(blocks) - i} more"
                break
            posts.append(current)
            current = block
        posts.append(current)
        return posts

    def render(self, style: Style) -> str:
        """The whole digest as one text."""
        return "\n\n".join(self.chunks(style, max_posts=1_000))

    def payload(self) -> dict[str, Any]:
        """The webhook body: the digest text plus every lead as JSON."""
        leads = []
        for item in self.listed:
            data = item.lead.to_dict()
            data["crm"] = {
                name: {"status": o.status, "id": o.record_id or None}
                for name, o in item.crm.items()
            }
            leads.append(data)
        return {
            "source": SOURCE,
            "kind": "leads",
            "day": self.day,
            "count": len(leads),
            "searches": self.searches,
            "waiting": self.waiting,
            "subject": self.subject,
            "text": self.render(PLAIN),
            "leads": leads,
        }
