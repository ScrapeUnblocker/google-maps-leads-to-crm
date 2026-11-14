"""google-maps-leads-to-crm: new local businesses from Google Maps, in your CRM.

Each run searches Google Maps for the kinds of business you sell to, in the places you
cover, through the ScrapeUnblocker Google Local API. It folds duplicates together, keeps
the businesses that pass your filters (rating, reviews, category, phone), skips the ones
already delivered or already in your CRM, looks up each new lead's website with one
Google search, and writes the new leads to HubSpot, Pipedrive or Airtable with a Slack,
Discord or webhook digest. A state file remembers what was sent.

    from google_maps_leads import load_config, search_businesses, merge, select
"""

from .collect import Lead, search_businesses, search_web
from .config import Config, ConfigError, Search, load_config
from .messages import Delivered, Digest, Outcome
from .process import merge, pick_website, rank, reject_reason, select
from .state import State

__version__ = "0.1.0"

__all__ = [
    "Config",
    "ConfigError",
    "Delivered",
    "Digest",
    "Lead",
    "Outcome",
    "Search",
    "State",
    "__version__",
    "load_config",
    "merge",
    "pick_website",
    "rank",
    "reject_reason",
    "search_businesses",
    "search_web",
    "select",
]
