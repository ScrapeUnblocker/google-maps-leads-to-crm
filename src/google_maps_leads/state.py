"""What has already been delivered, kept in one small JSON file.

The workflow commits ``state/leads.json`` back to the repo, so it survives between
scheduled runs. It holds:

- ``leads``: every business already delivered (or found already in your CRM), by lead id,
  with every phone and name key that points at it, the day it was first delivered and
  what each CRM did with it (``created:<record id>`` or ``exists:<record id>``).
- ``cursor``: where the next run starts in your list of searches, when
  ``searches.per_run`` spreads a long list over several runs.

Leads are never dropped from the file, so a business that leaves Google's top 20 and
comes back later is still not delivered twice. The file only changes when something was
delivered or the cursor moved.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .collect import Lead

VERSION = 1


class StateError(RuntimeError):
    """The state file exists but cannot be read."""


@dataclass
class LeadRecord:
    name: str
    first_seen: str  # YYYY-MM-DD
    search: str = ""
    ids: list[str] = field(default_factory=list)  # extra identities (other phones, name key)
    crm: dict[str, str] = field(default_factory=dict)  # "hubspot" -> "created:123"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "first_seen": self.first_seen,
            "search": self.search,
            "ids": self.ids,
            "crm": dict(sorted(self.crm.items())),
        }

    @classmethod
    def from_dict(cls, raw: Any, where: str) -> LeadRecord:
        if not isinstance(raw, dict):
            raise StateError(f"{where}: expected an object")
        ids = raw.get("ids") or []
        crm = raw.get("crm") or {}
        if not isinstance(ids, list) or not isinstance(crm, dict):
            raise StateError(f"{where}: ids must be a list and crm an object")
        return cls(
            name=str(raw.get("name") or ""),
            first_seen=str(raw.get("first_seen") or ""),
            search=str(raw.get("search") or ""),
            ids=[str(i) for i in ids],
            crm={str(k): str(v) for k, v in crm.items()},
        )


@dataclass
class State:
    leads: dict[str, LeadRecord] = field(default_factory=dict)
    cursor: int = 0
    _index: set[str] | None = field(default=None, repr=False, compare=False)

    @property
    def known(self) -> set[str]:
        """Every identity of every delivered lead."""
        if self._index is None:
            self._index = set()
            for key, record in self.leads.items():
                self._index.add(key)
                self._index.update(record.ids)
        return self._index

    def is_known(self, lead: Lead) -> bool:
        return any(k in self.known for k in lead.lookups)

    def add(self, lead: Lead, crm: dict[str, str], day: str) -> None:
        identities = lead.identities
        self.leads[lead.key] = LeadRecord(
            name=lead.name,
            first_seen=day,
            search=lead.search,
            ids=identities[1:],
            crm=dict(crm),
        )
        self.known.update(identities)

    @classmethod
    def load(cls, path: str | Path) -> State:
        path = Path(path)
        if not path.is_file():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"cannot read {path}: {exc}") from None
        if not isinstance(raw, dict) or not isinstance(raw.get("leads", {}), dict):
            raise StateError(f"{path}: not a leads state file")
        cursor = raw.get("cursor") or 0
        if not isinstance(cursor, int) or cursor < 0:
            raise StateError(f"{path}: cursor must be a whole number")
        leads = {
            str(key): LeadRecord.from_dict(value, f"{path} leads[{key!r}]")
            for key, value in (raw.get("leads") or {}).items()
        }
        return cls(leads=leads, cursor=cursor)

    def to_json(self) -> str:
        data = {
            "version": VERSION,
            "cursor": self.cursor,
            "leads": {key: self.leads[key].to_dict() for key in sorted(self.leads)},
        }
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    def save(self, path: str | Path) -> bool:
        """Write the file when its content changed. Returns True when it was written."""
        path = Path(path)
        text = self.to_json()
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            return False
        if not path.is_file() and not self.leads and not self.cursor:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
        return True
