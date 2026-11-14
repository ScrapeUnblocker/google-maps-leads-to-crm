"""Command line: ``google-maps-leads run | preview | status | test-destinations``."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from datetime import UTC, datetime
from typing import Any, TextIO

from scrapeunblocker import Client

from . import __version__
from .collect import (
    FATAL,
    FetchError,
    Lead,
    LeadClient,
    search_businesses,
    search_web,
    website_query,
)
from .config import Config, ConfigError, Search, load_config
from .messages import PLAIN, Delivered, Digest, Outcome, plural
from .process import merge, pick_website, reject_reason, search_words, select
from .sinks import REGISTRY, DeliveryError, Sinks, build_sinks, new_http_client
from .state import State, StateError

API_KEY_ENV = "SCRAPEUNBLOCKER_KEY"
SIGNUP_URL = (
    "https://scrapeunblocker.com/?utm_source=github&utm_medium=integration"
    "&utm_campaign=example-repos"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="google-maps-leads",
        description=(
            "New local businesses from Google Maps into HubSpot, Pipedrive or Airtable, "
            "deduped, with a Slack, Discord or webhook digest. Businesses come from the "
            "ScrapeUnblocker Google Local API."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-c", "--config", default="config.yml", help="config file (default: config.yml)"
    )
    common.add_argument(
        "--env-file",
        default=".env",
        help="read environment variables from this file when it exists (default: .env)",
    )
    common.add_argument("-v", "--verbose", action="store_true", help="log every API call")

    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    run = sub.add_parser("run", parents=[common], help="run the searches and deliver the new leads")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="print exactly what would be delivered; deliver nothing and save nothing",
    )
    run.add_argument(
        "--max-new",
        type=int,
        metavar="N",
        help="deliver at most N new leads this run (overrides delivery.max_new_per_run)",
    )
    preview = sub.add_parser(
        "preview",
        parents=[common],
        help="run one search and show which businesses pass your filters (saves nothing)",
    )
    preview.add_argument("query", help='a Google Maps search, e.g. "dentists in Denver, CO"')
    sub.add_parser("status", parents=[common], help="what the state file knows (no API calls)")
    sub.add_parser(
        "test-destinations",
        parents=[common],
        help="check each destination's connection (no ScrapeUnblocker API calls)",
    )
    return parser


def load_env_file(path: str, environ: MutableMapping[str, str]) -> None:
    """Minimal ``.env`` reader: ``KEY=value`` lines; real environment variables win."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.removeprefix("export ").strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in environ:
                environ[key] = value


def annotation(level: str, title: str, message: str) -> str:
    """A GitHub Actions workflow command (``::warning title=...::...``), safely escaped."""

    def data(text: str) -> str:
        return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")

    prop = data(title).replace(":", "%3A").replace(",", "%2C")
    return f"::{level} title={prop}::{data(message)}"


def short(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def pick_searches(
    searches: Sequence[Search], per_run: int, cursor: int
) -> tuple[list[Search], int]:
    """This run's searches and the cursor for the next run (``searches.per_run``)."""
    total = len(searches)
    if not per_run or per_run >= total:
        return list(searches), 0
    start = cursor % total
    batch = [searches[(start + i) % total] for i in range(per_run)]
    return batch, (start + per_run) % total


def parse_query(text: str) -> Search:
    what, sep, where = " ".join(text.split()).rpartition(" in ")
    return Search(what, where) if sep and what and where else Search(" ".join(text.split()))


# -- run ----------------------------------------------------------------------------------


def run_leads(
    config: Config,
    client: LeadClient,
    sinks: Sinks,
    *,
    dry_run: bool,
    now: datetime,
    max_new: int | None = None,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
    annotate: bool = False,
    summary_path: str | None = None,
    sleep: Callable[[float], None] | None = None,
) -> int:
    """One scheduled run: search, filter, look up websites, deliver, save. Returns the exit
    code."""
    today = now.date()
    day = today.isoformat()
    state = State.load(config.state)
    searches = config.searches
    every = searches.all()
    batch, next_cursor = pick_searches(every, searches.per_run, state.cursor)
    rotating = len(batch) < len(every)
    note = f" ({len(batch)} of {len(every)} this run, searches.per_run)" if rotating else ""
    print(
        f"Searching Google Maps: {plural(len(batch), 'search', 'searches')}, "
        f"1 API request each{note}",
        file=out,
    )
    found: list[Lead] = []
    failed: list[tuple[Search, FetchError]] = []
    requests = 0
    for search in batch:
        try:
            leads = search_businesses(
                client,
                search,
                country=searches.country,
                language=searches.language,
                today=today,
                sleep=sleep,
            )
        except FetchError as exc:
            failed.append((search, exc))
            print(f"  {short(search.query, 46):<46}  failed: {exc}", file=out)
            if annotate:
                print(annotation("warning", f"Search failed: {search.query}", str(exc)), file=out)
            continue
        requests += 1
        print(
            f"  {short(search.query, 46):<46}  {plural(len(leads), 'business', 'businesses')}",
            file=out,
        )
        found += leads

    chosen = select(merge(found), config.filters, state.known)
    limit = max_new or config.delivery.max_new_per_run
    take, waiting = chosen.new[:limit], max(0, len(chosen.new) - limit)
    later = f": {len(take)} now, {waiting} on the next run (max_new_per_run)" if waiting else ""
    print(
        f"{plural(chosen.found, 'business', 'businesses')} found, {len(chosen.passed)} match "
        f"your filters, {len(chosen.new)} new{later}",
        file=out,
    )
    if chosen.rejected:
        reasons = ", ".join(f"{n} {why}" for why, n in sorted(chosen.rejected.items()))
        print(f"Filtered out: {reasons}", file=out)

    todo = [lead for lead in take if not lead.website] if config.enrich.website else []
    if todo:
        print(
            f"Looking up websites: {plural(len(todo), 'Google search', 'Google searches')}, "
            "1 API request each",
            file=out,
        )
    for lead in todo:
        try:
            organic = search_web(client, website_query(lead), country=searches.country, sleep=sleep)
        except FetchError as exc:
            print(f"  {short(lead.name, 46):<46}  lookup failed: {exc}", file=out)
            continue
        requests += 1
        lead.website_checked = True
        url = pick_website(lead, organic, search_words(lead.search))
        if url:
            lead.website, lead.website_source = url, "google_search"
        print(f"  {short(lead.name, 46):<46}  {lead.domain or 'no website found'}", file=out)
    print(f"API requests used: {requests}", file=out)

    exit_code = 0
    if failed and (len(failed) == len(batch) or any(e.permanent for _, e in failed)):
        exit_code = 1

    def finish() -> int:
        if dry_run:
            print("Dry run: nothing was sent or saved.", file=out)
            return exit_code
        state.cursor = next_cursor
        if state.save(config.state):
            print(f"Saved {config.state.name}.", file=out)
        return exit_code

    print(file=out)
    if not take:
        print("Nothing new to deliver.", file=out)
        return finish()

    labels = {sink.name: sink.label for sink in sinks.crm}
    if not sinks.any:
        digest = Digest(day, [s.query for s in batch], [Delivered(x) for x in take], {}, waiting)
        print(
            f"{plural(len(take), 'new lead')} found, but no destination secret is set, so "
            "nothing was delivered. They are delivered on the first run after you add one:\n",
            file=out,
        )
        print(digest.render(PLAIN), file=out)
        return finish()

    # 1. CRMs: one record per lead, unless the CRM has it already.
    items: list[Delivered] = []
    crm_failed: dict[str, list[str]] = {}
    for lead in take:
        item = Delivered(lead)
        for sink in sinks.crm:
            try:
                existing = sink.find(lead)
                if existing:
                    item.crm[sink.name] = Outcome("exists", existing)
                elif dry_run:
                    item.crm[sink.name] = Outcome("would_create")
                else:
                    item.crm[sink.name] = Outcome("created", sink.create(lead))
            except DeliveryError as exc:
                print(f"Delivery failed: {exc}", file=err)
                crm_failed.setdefault(sink.label, []).append(lead.name)
        items.append(item)
    for sink in sinks.crm:
        for warning in dict.fromkeys(sink.warnings):
            print(f"{sink.label}: {warning}", file=out)
            if annotate:
                print(annotation("warning", sink.label, warning), file=out)

    # A lead is done once a CRM has it. With no CRM, the chat digest is the delivery.
    accepted = [item for item in items if item.crm] if sinks.crm else items
    digest = Digest(day, [s.query for s in batch], accepted, labels, waiting)

    if dry_run:
        print(f"Dry run: would deliver {plural(len(take), 'new lead')}.", file=out)
        for sink in sinks.crm:
            print(f"\n--- {sink.label} ---", file=out)
            for item in items:
                outcome = item.crm.get(sink.name)
                if outcome is None:
                    print(f"{item.lead.name}: check failed, see above", file=out)
                elif outcome.status == "exists":
                    print(
                        f"{item.lead.name}: already there ({outcome.record_id}), skipped", file=out
                    )
                else:
                    body = json.dumps(sink.preview(item.lead), ensure_ascii=False)
                    print(f"{item.lead.name}: would create {body}", file=out)
        for sink in sinks.notify:
            posts = sink.preview(digest) if digest.listed else []
            print(f"\n--- {sink.name} ---", file=out)
            print("\n\n(next post)\n\n".join(posts) if posts else "(nothing to post)", file=out)
        print(file=out)
        return finish()

    if sinks.crm:
        parts = []
        for sink in sinks.crm:
            done = [i.crm[sink.name].status for i in items if sink.name in i.crm]
            created, there = done.count("created"), done.count("exists")
            parts.append(f"{sink.label}: {created} created, {there} already there")
        print("; ".join(parts), file=out)

    notified: list[str] = []
    notify_failed: list[str] = []
    if digest.listed:
        for sink in sinks.notify:
            try:
                sink.send(digest)
            except DeliveryError as exc:
                print(f"Delivery failed: {exc}", file=err)
                notify_failed.append(sink.name)
            else:
                notified.append(sink.name)
        if notified:
            print(
                f"Digest of {plural(len(digest.listed), 'lead')} sent to {', '.join(notified)}.",
                file=out,
            )
    elif accepted:
        print("Every new lead was already in your CRM; no digest to post.", file=out)

    done = accepted if sinks.crm else (accepted if notified else [])
    for item in done:
        state.add(item.lead, {n: o.to_state() for n, o in item.crm.items()}, day)
    missed = len(take) - len(done)
    if missed:
        print(f"Not delivered: {plural(missed, 'lead')}. They are tried again next run.", file=out)

    if crm_failed or notify_failed:
        exit_code = 1
        if annotate:
            names = sorted(crm_failed) + notify_failed
            print(
                annotation("warning", "Delivery failed", f"Not accepted by {', '.join(names)}"),
                file=out,
            )
        for label, names in crm_failed.items():
            print(f"{label} did not take: {', '.join(names)}", file=out)

    if summary_path:
        _write_summary(summary_path, digest, failed)
    return finish()


def _write_summary(path: str, digest: Digest, failed: Sequence[tuple[Search, FetchError]]) -> None:
    lines = ["### Google Maps leads", "", f"{digest.subject}.", ""]
    crm = digest.crm_line()
    if crm:
        lines += [crm, ""]
    if digest.listed:
        lines += ["| Business | Category | Rating | Phone | Website |", "|---|---|---|---|---|"]
        for item in digest.listed:
            lead = item.lead
            site = f"[{lead.domain}]({lead.website})" if lead.website else ""
            name = lead.name.replace("|", "/")
            lines.append(
                f"| [{name}]({lead.maps_url}) | {lead.category} | {lead.rating_text()} | "
                f"{lead.phone} | {site} |"
            )
    for search, exc in failed:
        lines.append(f"\nSearch failed: {search.query}: {str(exc)[:200]}")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# -- other commands -----------------------------------------------------------------------


def preview_search(
    config: Config, client: LeadClient, query: str, now: datetime, out: TextIO
) -> int:
    search = parse_query(query)
    state = State.load(config.state)
    try:
        leads = search_businesses(
            client,
            search,
            country=config.searches.country,
            language=config.searches.language,
            today=now.date(),
        )
    except FetchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    merged = merge(leads)
    print(f'Google Maps: "{search.query}" ({config.searches.country})\n', file=out)
    passing = new = 0
    for lead in merged:
        reason = reject_reason(lead, config.filters)
        if reason:
            verdict = reason
        elif state.is_known(lead):
            verdict = "already delivered"
            passing += 1
        else:
            verdict = "NEW"
            passing += 1
            new += 1
        ad = " (ad)" if lead.sponsored else ""
        print(
            f"  {short(lead.name + ad, 40):<40}  {lead.rating_text():<22}  "
            f"{lead.phone or '-':<16}  {verdict}",
            file=out,
        )
    print(
        f"\n{plural(len(merged), 'business', 'businesses')}, {passing} match your filters, "
        f"{new} new. API requests used: 1. Nothing was saved.",
        file=out,
    )
    if search.where:
        print(
            f"Add it to config.yml: searches.what: {search.what!r}, "
            f"searches.where: {search.where!r}",
            file=out,
        )
    return 0


def show_status(config: Config, out: TextIO) -> int:
    state = State.load(config.state)
    every = config.searches.all()
    print(f"State: {config.state}", file=out)
    if not state.leads:
        print("No leads delivered yet.", file=out)
    else:
        days = sorted(r.first_seen for r in state.leads.values() if r.first_seen)
        span = f", first on {days[0]}, latest on {days[-1]}" if days else ""
        print(f"{plural(len(state.leads), 'lead')} delivered{span}", file=out)
        per_crm: dict[str, dict[str, int]] = {}
        for record in state.leads.values():
            for name, value in record.crm.items():
                status = value.split(":", 1)[0]
                per_crm.setdefault(name, {}).setdefault(status, 0)
                per_crm[name][status] += 1
        for name, counts in per_crm.items():
            text = ", ".join(f"{n} {status}" for status, n in sorted(counts.items()))
            print(f"  {name}: {text}", file=out)
    per_run = config.searches.per_run
    if per_run and per_run < len(every):
        nxt = every[state.cursor % len(every)]
        print(
            f"Searches: {len(every)}, {per_run} per run; the next run starts with "
            f'"{nxt.query}" (#{state.cursor % len(every) + 1})',
            file=out,
        )
    else:
        print(f"Searches: {len(every)}, all of them every run", file=out)
    return 0


def test_destinations(sinks: Sinks, out: TextIO) -> int:
    for name, missing in sinks.skipped.items():
        print(f"  {name:<9} skipped ({missing} not set)", file=out)
    failed = 0
    for sink in [*sinks.crm, *sinks.notify]:
        try:
            result = sink.test()
        except DeliveryError as exc:
            failed += 1
            print(f"  {sink.name:<9} FAILED {exc}", file=out)
        else:
            print(f"  {sink.name:<9} ok, {result}", file=out)
        for warning in getattr(sink, "warnings", []):
            print(f"  {'':<9} note: {warning}", file=out)
    return 1 if failed else 0


def _make_client(environ: Mapping[str, str]) -> Client:
    return Client(api_key=environ[API_KEY_ENV].strip(), timeout=180.0)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: MutableMapping[str, str] | None = None,
    client_factory: Callable[[Mapping[str, str]], LeadClient] = _make_client,
    http_factory: Callable[[], Any] = new_http_client,
    now: datetime | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    environ = os.environ if environ is None else environ
    out = out or sys.stdout
    err = err or sys.stderr
    for stream in (out, err):
        if hasattr(stream, "reconfigure"):
            # Business names can hold any character; never crash on a narrow console.
            stream.reconfigure(errors="replace")
    now = now or datetime.now(UTC).replace(microsecond=0)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full request URLs at INFO, and webhook URLs are secrets.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    load_env_file(args.env_file, environ)
    has_key = bool((environ.get(API_KEY_ENV) or "").strip())
    try:
        config = load_config(args.config)
        if args.command == "status":
            return show_status(config, out)
        if args.command in ("run", "preview") and not has_key:
            print(
                f"error: set {API_KEY_ENV} to your ScrapeUnblocker API key "
                f"(get one at {SIGNUP_URL})",
                file=err,
            )
            return 2
        if args.command == "preview":
            return preview_search(config, client_factory(environ), args.query, now, out)
        if args.command == "run" and args.max_new is not None and args.max_new < 1:
            raise ConfigError("--max-new must be 1 or more")

        http = http_factory()
        try:
            sinks = build_sinks(config.delivery, environ, http)
            if args.command == "test-destinations":
                if not sinks.any:
                    wanted = "; ".join(
                        " + ".join(REGISTRY[n].env) for n in config.delivery.destinations
                    )
                    print(f"error: no destination is set up. Set one of: {wanted}", file=err)
                    return 2
                return test_destinations(sinks, out)
            for name, missing in sinks.skipped.items():
                print(f"({name} skipped: {missing} not set)", file=out)
            return run_leads(
                config,
                client_factory(environ),
                sinks,
                dry_run=args.dry_run,
                now=now,
                max_new=args.max_new,
                out=out,
                err=err,
                annotate=environ.get("GITHUB_ACTIONS") == "true",
                summary_path=environ.get("GITHUB_STEP_SUMMARY") or None,
            )
        finally:
            close = getattr(http, "close", None)
            if callable(close):
                close()
    except (ConfigError, StateError, DeliveryError) as exc:
        print(f"error: {exc}", file=err)
        return 2
    except FATAL as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=err)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
