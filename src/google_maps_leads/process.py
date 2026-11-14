"""What a raw Google Maps search does not give you.

- :func:`merge` folds the same business found twice (in two searches, or as an ad and as
  a normal result) into one lead.
- :func:`reject_reason` applies the filters from ``config.yml``.
- :func:`rank` puts the most established businesses first.
- :func:`pick_website` finds a lead's own site among Google search results, skipping
  directories and social profiles.
"""

from __future__ import annotations

import re
from collections.abc import Container, Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from .collect import Lead, digits, norm
from .config import Filters, Search

# Sites that list businesses but are not the business's own site.
DIRECTORIES = frozenset(
    {
        "about.me",
        "alignable.com",
        "angi.com",
        "angieslist.com",
        "apple.com",
        "bark.com",
        "bbb.org",
        "bing.com",
        "birdeye.com",
        "bizapedia.com",
        "brownbook.net",
        "buildzoom.com",
        "buzzfile.com",
        "chamberofcommerce.com",
        "citysearch.com",
        "crunchbase.com",
        "demandforce.com",
        "dnb.com",
        "doordash.com",
        "expertise.com",
        "facebook.com",
        "foursquare.com",
        "glassdoor.com",
        "google.com",
        "groupon.com",
        "grubhub.com",
        "healthgrades.com",
        "homeadvisor.com",
        "hotfrog.com",
        "houzz.com",
        "indeed.com",
        "instagram.com",
        "linkedin.com",
        "manta.com",
        "mapquest.com",
        "merchantcircle.com",
        "networx.com",
        "nextdoor.com",
        "opencorporates.com",
        "opentable.com",
        "pinterest.com",
        "porch.com",
        "quora.com",
        "reddit.com",
        "showmelocal.com",
        "superpages.com",
        "threebestrated.com",
        "thumbtack.com",
        "tiktok.com",
        "tripadvisor.com",
        "trustpilot.com",
        "trusty.app",
        "twitter.com",
        "ubereats.com",
        "vitals.com",
        "wheree.com",
        "wikipedia.org",
        "x.com",
        "yably.com",
        "yell.com",
        "yellowpages.com",
        "yelp.com",
        "youtube.com",
        "zocdoc.com",
        "zoominfo.com",
    }
)

# Website builders where a small business's site is a subdomain (name.weebly.com).
SITE_BUILDERS = frozenset(
    {
        "blogspot.com",
        "business.site",
        "carrd.co",
        "godaddysites.com",
        "jimdosite.com",
        "square.site",
        "squarespace.com",
        "webflow.io",
        "weebly.com",
        "wixsite.com",
        "wordpress.com",
    }
)

# Words too common in business names to tie a name to a domain on their own.
GENERIC = frozenset(
    """
    and the of for at in on by with
    company companies co corp corporation inc llc ltd limited group services service
    solutions pros pro team center centre clinic studio shop store office offices
    associates partners family local best quality professional professionals expert
    experts home homes house city county metro north south east west central
    """.split()
)

_SECOND_LEVEL = frozenset({"co", "com", "net", "org", "gov", "edu", "ac", "or", "ne", "gob"})


# -- merge, filter, rank ------------------------------------------------------------------


def _absorb(into: Lead, other: Lead) -> None:
    into.sponsored = into.sponsored or other.sponsored
    into.from_ad = into.from_ad and other.from_ad
    for phone in [other.phone, *other.other_phones]:
        if phone and digits(phone) != into.phone_digits and phone not in into.other_phones:
            into.other_phones.append(phone)
    into.street = into.street or other.street
    into.locality = into.locality or other.locality
    into.years_in_business = into.years_in_business or other.years_in_business
    into.open_24h = into.open_24h or other.open_24h
    if not into.website and other.website:
        into.website, into.website_source = other.website, other.website_source
        into.website_checked = True
    for query in other.searches:
        if query not in into.searches:
            into.searches.append(query)


def merge(leads: Iterable[Lead]) -> list[Lead]:
    """One lead per business. Normal results win over ads (an ad can show a call-tracking
    number), so ads are folded in after them. A shared phone, the same name in the same
    searched place, or an ad with the name of a business found anywhere marks a
    duplicate."""
    leads = list(leads)
    ordered = [lead for lead in leads if not lead.sponsored] + [
        lead for lead in leads if lead.sponsored
    ]
    out: list[Lead] = []
    index: dict[str, Lead] = {}
    for lead in ordered:
        match = next((index[k] for k in lead.lookups if k in index), None)
        if match is None:
            out.append(lead)
            match = lead
        else:
            _absorb(match, lead)
        for key in match.identities:
            index.setdefault(key, match)
    return out


def _contains_any(text: str, needles: Sequence[str]) -> bool:
    low = text.lower()
    return any(n.lower() in low for n in needles)


def reject_reason(lead: Lead, filters: Filters) -> str | None:
    """Why a lead does not pass the filters, or None when it does."""
    if filters.sponsored == "exclude" and lead.sponsored:
        return "runs ads (sponsored: exclude)"
    if filters.sponsored == "only" and not lead.sponsored:
        return "no ads (sponsored: only)"
    if filters.require_phone and not lead.phone:
        return "no phone"
    if filters.min_rating and (lead.rating is None or lead.rating < filters.min_rating):
        return f"rating below {filters.min_rating:g}"
    reviews = lead.reviews or 0
    if reviews < filters.min_reviews:
        return f"fewer than {filters.min_reviews} reviews"
    if filters.max_reviews and reviews > filters.max_reviews:
        return f"more than {filters.max_reviews} reviews"
    if filters.categories and not _contains_any(lead.category, filters.categories):
        return "category not in filters.categories"
    if filters.exclude_categories and _contains_any(lead.category, filters.exclude_categories):
        return "category excluded"
    if filters.exclude_names and _contains_any(lead.name, filters.exclude_names):
        return "name excluded"
    return None


def rank(leads: Iterable[Lead]) -> list[Lead]:
    """Most reviews first, then the higher rating, then Google's own order."""
    return sorted(
        leads,
        key=lambda lead: (
            -(lead.reviews or 0),
            -(lead.rating or 0.0),
            lead.position if lead.position is not None else 999,
        ),
    )


@dataclass
class Selection:
    """The outcome of filtering one run's businesses."""

    found: int  # businesses after merging
    passed: list[Lead]  # match the filters
    new: list[Lead]  # ... and were never delivered, ranked
    rejected: dict[str, int]  # reason -> count


def select(leads: Sequence[Lead], filters: Filters, known: Container[str]) -> Selection:
    """Filter merged leads and keep the ones whose identities are all unknown."""
    passed: list[Lead] = []
    rejected: dict[str, int] = {}
    for lead in leads:
        reason = reject_reason(lead, filters)
        if reason:
            rejected[reason] = rejected.get(reason, 0) + 1
        else:
            passed.append(lead)
    new = [lead for lead in passed if not any(k in known for k in lead.lookups)]
    return Selection(found=len(leads), passed=passed, new=rank(new), rejected=rejected)


# -- website matching ---------------------------------------------------------------------


def registrable(host: str) -> str:
    """``www.shop.example.co.uk`` -> ``example.co.uk``; good enough for matching."""
    parts = host.lower().strip(".").removeprefix("www.").split(".")
    if len(parts) >= 3 and parts[-2] in _SECOND_LEVEL and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _label(domain: str) -> str:
    return domain.split(".")[0].replace("-", "")


def search_words(search: Search | str) -> set[str]:
    """Words of the search itself ("roofing", "austin"): they say nothing about one business."""
    words = set(norm(str(search)).split())
    # "roofing contractors" also covers "roofer", "contractor".
    words |= {w[:-1] for w in words if w.endswith("s")}
    return words


def _distinct_tokens(name: str, ignore: set[str]) -> list[str]:
    """Name words that can identify one business: not generic, and not a word of the search
    ("dental" for a "dentists" search shares the stem "dent")."""
    stems = {w[:4] for w in ignore if len(w) >= 4}
    return [
        w
        for w in norm(name).split()
        if len(w) >= 4
        and w not in GENERIC
        and w not in ignore
        and w[:4] not in stems
        and not w.isdigit()
    ]


_PHONE = re.compile(r"\+?\d[\d().\s-]{5,}\d")


def _phone_numbers(text: str) -> list[str]:
    return [digits(m.group(0)) for m in _PHONE.finditer(text)]


def _confirms(lead: Lead, text: str) -> bool:
    """The result shows this business's phone, street or town, so the name match is not
    a same-named business somewhere else."""
    phones = [d[-7:] for d in (digits(p) for p in [lead.phone, *lead.other_phones]) if len(d) >= 7]
    if any(n.endswith(p) for n in _phone_numbers(text) for p in phones):
        return True
    words = f" {norm(text)} "
    street = norm(lead.street).split()[:2]
    if len(street) == 2 and f" {' '.join(street)} " in words:
        return True
    town = norm(lead.city or lead.where.split(",")[0])
    return bool(town) and f" {town} " in words


def pick_website(lead: Lead, organic: Sequence[dict], ignore: set[str]) -> str | None:
    """The business's own homepage from Google search results, or None.

    A result counts when it is not a directory or social site, its domain carries the
    first distinctive word of the business name ("kiddroof.com" for Kidd Roofing) or
    spells the name ("austinroofingandconstruction.com"), and its title or snippet shows the
    business's phone (the last 7 digits, so a toll-free variant matches too), street or
    town. A wrong website is worse than none, so anything less is not taken.
    """
    tokens = _distinct_tokens(lead.name, ignore)
    name_slug = norm(lead.name).replace(" ", "")
    for result in organic:
        url = str(result.get("url") or "")
        host = (urlsplit(url).hostname or "").lower()
        if not host or not url.startswith(("http://", "https://")):
            continue
        domain = registrable(host)
        if domain in DIRECTORIES or any(host.endswith("." + d) for d in DIRECTORIES):
            continue
        # On a site builder the business is the subdomain: name.weebly.com.
        label = _label(host.removeprefix("www.") if domain in SITE_BUILDERS else domain)
        # The first distinctive word is usually the brand ("Comfort" Dental Mile High),
        # so it must be there, unless every distinctive word is.
        by_name = bool(tokens) and (tokens[0] in label or all(t in label for t in tokens))
        by_slug = len(name_slug) >= 6 and (
            label.startswith(name_slug)
            or (name_slug.startswith(label) and len(label) >= 0.6 * len(name_slug))
        )
        text = f"{result.get('title') or ''} {result.get('description') or ''} {url}"
        if (by_name or by_slug) and _confirms(lead, text):
            return f"{urlsplit(url).scheme}://{host}/"
    return None
